<#
  TRDGO.US - deploy 38ca61d (UW unlimited-plan warmer fix) over db6a72c.   REVIEW COPY: not executed by Claude.

  Modes (default is the harmless one):
    -Mode Preflight   read-only: verifies everything, changes NOTHING                         (default)
    -Mode Deploy      needs -Confirm 'DEPLOY 38ca61d' and an elevated shell
    -Mode PostCheck   read-only: health, trades, audit table, warmer, auth, log
    -Mode Rollback    needs -Confirm 'ROLLBACK TO db6a72c': code + frontend only

  Never: touches the database schema or data (SELECT only), restores the dump, edits .env, bypasses
  authentication, touches another service, prints a secret.
#>
param(
    [ValidateSet('Preflight', 'Deploy', 'PostCheck', 'Rollback')][string]$Mode = 'Preflight',
    [string]$Confirm = '',
    [string]$ZipSha256 = '',          # optional: pin the exact frontend rollback ZIP
    [int]$MinTrades = 1021,           # baseline floor (the live baseline is read and used when higher)
    [int]$MinMaxId = 1023
)

$ErrorActionPreference = 'Continue'   # native commands write to stderr; every step checks its own result
Set-StrictMode -Version 2.0

# ------------------------------------------------------------------ constants (all reviewed values) ----
$R       = 'C:\apps\us-stock-reader'
$Base    = 'db6a72ca12f77d523c3e17ef6e3e6dfa365e9e12'
$Target  = '38ca61df44029153e65c059d9bb2ce11210bb2bc'
$Svc     = 'USStockReader'
$Dump    = 'C:\audit-reports\db-backups\trdgo_us_20261028-1552_main_pre_uw_fix.dump'
$Zip     = 'C:\audit-reports\frontend-backups\dist_pre_uw_fix_20261028.zip'
$FpPy    = 'C:\audit-reports\phaseC\prod_fingerprint.py'
$Py      = "$R\backend\.venv\Scripts\python.exe"
$Dist    = "$R\frontend\dist"
$EnvFile = "$R\backend\.env"
$ErrLog  = "$R\logs\service.err.log"
$LogDir  = 'C:\audit-reports\deploy_uw_fix'
$Expected = @('backend/api_routes.py', 'backend/local_warmer.py', 'backend/tab_settings.py',
              'backend/tests/test_universe_warmer.py', 'backend/tests/test_uw_warmer_plan.py',
              'backend/unusualwhales_service.py', 'frontend/src/pages/AllModelsPage.tsx',
              'frontend/src/pages/all-models.css') | Sort-Object
$Changed = @('backend\api_routes.py', 'backend\local_warmer.py', 'backend\tab_settings.py', 'backend\unusualwhales_service.py')
$HealthWaitS = 90; $StopWaitS = 60; $BuildTimeoutS = 600

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp   = Get-Date -Format 'yyyyMMdd-HHmmss'
$LogFile = Join-Path $LogDir "deploy_$Mode`_$Stamp.log"
$StateFile = Join-Path $LogDir 'deploy_state.json'
$script:Fails = New-Object System.Collections.ArrayList
$script:Baseline = $null; $script:EnvSha = ''; $script:TbBefore = $null; $script:OrigBranch = ''; $script:GitCode = 0
$script:Warns = New-Object System.Collections.ArrayList

function Log([string]$m, [string]$lvl = 'INFO') {
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'HH:mm:ss'), $lvl, $m
    Add-Content -Path $LogFile -Value $line
    $c = 'Gray'; if ($lvl -eq 'OK') { $c = 'Green' } elseif ($lvl -eq 'WARN') { $c = 'Yellow' } elseif ($lvl -eq 'FAIL' -or $lvl -eq 'STOP') { $c = 'Red' }
    Write-Host $line -ForegroundColor $c
}
function Fail([string]$m) { [void]$script:Fails.Add($m); Log $m 'FAIL' }
function Warn([string]$m) { [void]$script:Warns.Add($m); Log $m 'WARN' }
function Stop-Run([string]$m) { Log $m 'STOP'; throw "STOP: $m" }
function Invoke-Git {      # simple function on purpose: an advanced function would swallow '-e' as -ErrorAction
    $out = & git -C $R @args 2>&1 | ForEach-Object { "$_" }
    $script:GitCode = $LASTEXITCODE
    return ($out -join "`n").Trim()
}
function Sha256File([string]$p) { (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLower() }
function Sha256Bytes([byte[]]$b) { $h = [Security.Cryptography.SHA256]::Create().ComputeHash($b); ([BitConverter]::ToString($h) -replace '-', '').ToLower() }

# ------------------------------------------------------------------ environment guard (names + allow-listed values only) ----
function Read-EnvFlags {
    $f = @{}
    foreach ($l in (Get-Content -LiteralPath $EnvFile)) {
        if ($l -match '^\s*(APP_ENV|INTRADAY_VERIFICATION_MODE|INTRADAY_WORKFLOW)\s*=\s*(.*?)\s*$') { $f[$Matches[1]] = $Matches[2].Trim('"', "'") }
    }
    return $f
}
function Assert-EnvGuard([string]$when) {
    $f = Read-EnvFlags
    $ok = ($f['APP_ENV'] -eq 'production') -and ($f['INTRADAY_VERIFICATION_MODE'] -eq 'audit') -and (($null -eq $f['INTRADAY_WORKFLOW']) -or ($f['INTRADAY_WORKFLOW'] -eq 'legacy'))
    Log ("env guard ($when): APP_ENV=$($f['APP_ENV'])  INTRADAY_VERIFICATION_MODE=$($f['INTRADAY_VERIFICATION_MODE'])  INTRADAY_WORKFLOW=$($f['INTRADAY_WORKFLOW'])")
    return $ok
}

# ------------------------------------------------------------------ frontend ZIP helpers ----
Add-Type -AssemblyName System.IO.Compression.FileSystem
function Get-ZipMap([string]$zipPath) {
    # relative path (under dist) -> SHA-256 of every file in the ZIP; handles a leading 'dist/' folder
    $map = @{}
    $z = [IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $names = @($z.Entries | Where-Object { $_.Name -ne '' } | ForEach-Object { $_.FullName.Replace('\', '/') })
        $prefix = ''
        if ($names.Count -gt 0 -and @($names | Where-Object { $_ -notlike 'dist/*' }).Count -eq 0) { $prefix = 'dist/' }
        foreach ($e in $z.Entries) {
            if ($e.Name -eq '') { continue }
            $rel = $e.FullName.Replace('\', '/'); if ($prefix -and $rel.StartsWith($prefix)) { $rel = $rel.Substring($prefix.Length) }
            $s = $e.Open(); try { $ms = New-Object IO.MemoryStream; $s.CopyTo($ms); $map[$rel] = Sha256Bytes $ms.ToArray() } finally { $s.Dispose() }
        }
    } finally { $z.Dispose() }
    return $map
}
function Get-DirMap([string]$dir) {
    $map = @{}; $root = (Resolve-Path -LiteralPath $dir).Path.TrimEnd('\')
    foreach ($f in Get-ChildItem -LiteralPath $dir -Recurse -File) { $map[$f.FullName.Substring($root.Length + 1).Replace('\', '/')] = Sha256File $f.FullName }
    return $map
}
function Same-Maps($a, $b) {
    if ($a.Count -ne $b.Count) { return $false }
    foreach ($k in $a.Keys) { if (-not $b.ContainsKey($k) -or $b[$k] -ne $a[$k]) { return $false } }
    return $true
}
function Restore-FrontendFromZip {
    $stash = Join-Path $LogDir "dist_replaced_$Stamp"
    if (Test-Path -LiteralPath $Dist) { Move-Item -LiteralPath $Dist -Destination $stash; Log "current dist kept for inspection: $stash" }
    New-Item -ItemType Directory -Path $Dist | Out-Null
    $root = (Resolve-Path -LiteralPath $Dist).Path.TrimEnd('\')
    $z = [IO.Compression.ZipFile]::OpenRead($Zip)
    try {
        $names = @($z.Entries | Where-Object { $_.Name -ne '' } | ForEach-Object { $_.FullName.Replace('\', '/') })
        $prefix = ''; if ($names.Count -gt 0 -and @($names | Where-Object { $_ -notlike 'dist/*' }).Count -eq 0) { $prefix = 'dist/' }
        foreach ($e in $z.Entries) {
            if ($e.Name -eq '') { continue }
            $rel = $e.FullName.Replace('\', '/'); if ($prefix -and $rel.StartsWith($prefix)) { $rel = $rel.Substring($prefix.Length) }
            $dest = [IO.Path]::GetFullPath((Join-Path $root $rel))
            if (-not $dest.StartsWith($root + '\')) { throw "ZIP entry escapes dist: $($e.FullName)" }    # zip-slip guard
            New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $dest, $true)
        }
    } finally { $z.Dispose() }
    return (Same-Maps (Get-ZipMap $Zip) (Get-DirMap $Dist))
}

# ------------------------------------------------------------------ service / health ----
function Get-SvcStatus { (Get-Service -Name $Svc -ErrorAction Stop).Status.ToString() }
function Wait-Health([int]$seconds) {
    $end = (Get-Date).AddSeconds($seconds); $raw = ''
    while ((Get-Date) -lt $end) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 5
            $raw = $r.Content
            if ($r.StatusCode -eq 200 -and $raw -match '"status"\s*:\s*"ok"' -and $raw -match '"database"\s*:\s*"connected"') { Log "health ok: $raw" 'OK'; return $true }
        } catch { $raw = $_.Exception.Message }
        Start-Sleep -Seconds 3
    }
    Log "health NOT ok after ${seconds}s: $raw" 'FAIL'; return $false
}
function Stop-Service-Safely {
    if ((Get-SvcStatus) -ne 'Stopped') {
        Stop-Service -Name $Svc -ErrorAction Stop
        $end = (Get-Date).AddSeconds($StopWaitS)
        while ((Get-SvcStatus) -ne 'Stopped' -and (Get-Date) -lt $end) { Start-Sleep -Seconds 1 }
    }
    if ((Get-SvcStatus) -ne 'Stopped') { Stop-Run "$Svc did not stop within ${StopWaitS}s" }
    Start-Sleep -Seconds 3
    # nothing from the production venv may still be running (only this app's processes are looked at, none are killed)
    $left = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -like "$R\backend\.venv\*" })
    if ($left.Count -gt 0) { Stop-Run "a process from the production venv is still running (PID $($left[0].ProcessId)); not switching code" }
    Log "$Svc stopped, no production-venv process left" 'OK'
}

# ------------------------------------------------------------------ read-only database reads (SELECT only) ----
$DbReadPy = @'
import json, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from database import SessionLocal
db = SessionLocal()
out = {}
try:
    c, m = db.execute(text("select count(*), coalesce(max(id), 0) from demo_trades")).first()
    out["trades"], out["max_id"] = int(c), int(m)
    out["audit_table"] = bool(db.execute(text("select to_regclass('public.intraday_signal_audit') is not null")).scalar())
    out["audit_rows"] = int(db.execute(text("select count(*) from intraday_signal_audit")).scalar()) if out["audit_table"] else None
    row = db.execute(text("select state from local_warm_state where id = 2")).first()
    st = json.loads(row[0]) if row else {}
    keep = ("status", "pause_reason", "total", "complete", "plan_unlimited", "calls_limit", "current", "updated_at", "last_success_at")
    out["warm"] = {k: st.get(k) for k in keep}
    sy = st.get("symbols") or {}
    out["warm"]["failed"] = sum(1 for v in sy.values() if v.get("s") == "failed")
    out["warm"]["partial"] = sum(1 for v in sy.values() if v.get("s") == "partial")
    out["warm"]["retrying"] = sum(1 for v in sy.values() if v.get("s") == "retry")
finally:
    db.rollback(); db.close()
print(json.dumps(out, default=str))
'@
function Read-Db {
    $f = Join-Path $LogDir 'db_read.py'; Set-Content -LiteralPath $f -Value $DbReadPy -Encoding UTF8
    $old = $env:APP_ENV; $env:APP_ENV = 'production'      # only for this child process; the guard has verified .env says production
    Push-Location "$R\backend"
    try { $o = & $Py $f 2>&1 | ForEach-Object { "$_" } } finally { Pop-Location; if ($null -eq $old) { Remove-Item Env:\APP_ENV -ErrorAction SilentlyContinue } else { $env:APP_ENV = $old } }
    $line = ($o | Where-Object { $_ -like '{*' } | Select-Object -Last 1)
    if (-not $line) { Log "database read returned no JSON (last line: $(($o | Select-Object -Last 1)))" 'WARN'; return $null }
    return ($line | ConvertFrom-Json)
}

# ------------------------------------------------------------------ PREFLIGHT (read-only) ----
function Invoke-Preflight {
    Log "Preflight  $Base -> $Target"
    $head = Invoke-Git rev-parse HEAD
    if ($head -ne $Base) { Fail "HEAD is $head, expected $Base" } else { Log "HEAD is the baseline" 'OK' }
    $dirty = @((Invoke-Git status --porcelain) -split "`n" | Where-Object { $_ }).Count
    if ($dirty -ne 0) { Fail "working tree not clean ($dirty paths)" } else { Log 'working tree clean' 'OK' }
    $null = Invoke-Git cat-file -e "$Target^{commit}"
    if ($script:GitCode -ne 0) { Fail 'target commit object is not present (run D1 fetch first)' }
    else {
        $tp = Invoke-Git rev-parse "$Target^"
        if ($tp -ne $Base) { Fail "target parent is $tp" } else { Log 'target parent is the baseline' 'OK' }
        $files = @((Invoke-Git diff --name-only $Base $Target) -split "`n" | Where-Object { $_ } | Sort-Object)
        if (($files -join '|') -ne ($Expected -join '|')) { Fail "diff is not the 8 approved files: $($files -join ', ')" } else { Log 'diff is exactly the 8 approved files' 'OK' }
    }
    if (-not (Assert-EnvGuard 'preflight')) { Fail 'APP_ENV / INTRADAY_VERIFICATION_MODE / INTRADAY_WORKFLOW are not production / audit / legacy' } else { Log 'env guard ok' 'OK' }
    $script:EnvSha = Sha256File $EnvFile; Log "env file SHA-256 (value never printed): $($script:EnvSha)"
    foreach ($p in @($Py, $FpPy, $Zip, $Dump)) { if (-not (Test-Path -LiteralPath $p)) { Fail "missing: $p" } }
    if (Test-Path -LiteralPath $Dump) { $d = Get-Item $Dump; if ($d.Length -lt 1MB) { Fail 'dump smaller than 1 MB' } else { Log ("dump present: $($d.Name)  $([math]::Round($d.Length/1MB,1)) MB  written $($d.LastWriteTime)") 'OK' } }
    if (Test-Path -LiteralPath $Zip) {
        $zs = Sha256File $Zip; Log "frontend ZIP SHA-256: $zs"
        if ($ZipSha256 -and $zs -ne $ZipSha256.ToLower()) { Fail 'frontend ZIP SHA-256 differs from -ZipSha256' }
        if (Test-Path -LiteralPath $Dist) {
            if (Same-Maps (Get-ZipMap $Zip) (Get-DirMap $Dist)) { Log 'ZIP matches the live dist byte for byte (rollback asset is exact)' 'OK' } else { Fail 'the frontend ZIP does not match the live dist: not an exact rollback asset' }
        } else { Fail 'live frontend dist missing' }
    }
    foreach ($c in 'node', 'npm') { if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { Fail "$c not found" } }
    if (-not (Test-Path -LiteralPath "$R\frontend\node_modules")) { Fail 'frontend\node_modules missing (this script never runs npm install)' }
    if ((Get-PSDrive C).Free -lt 2GB) { Fail 'less than 2 GB free on C:' } else { Log ("free C: $([math]::Round((Get-PSDrive C).Free/1GB,1)) GB") 'OK' }
    if ((Get-SvcStatus) -ne 'Running') { Fail "$Svc is $(Get-SvcStatus), expected Running" } else { Log "$Svc is Running" 'OK' }
    if (-not (Wait-Health 15)) { Fail 'health is not ok before deployment' }
    $db = Read-Db
    if ($null -eq $db) { Fail 'baseline database read failed' }
    else {
        Log "baseline: trades=$($db.trades) max_id=$($db.max_id) audit_table=$($db.audit_table) audit_rows=$($db.audit_rows)"
        if ($db.trades -lt $MinTrades -or $db.max_id -lt $MinMaxId) { Fail "trades below the reported baseline ($MinTrades / $MinMaxId)" }
        if (-not $db.audit_table) { Fail 'intraday_signal_audit is missing' }
        $script:Baseline = $db
    }
    $script:TbBefore = @(Select-String -Path $ErrLog -Pattern 'Traceback \(most recent call last\)').Count
    Log "error-log Tracebacks before: $($script:TbBefore)"
    return ($script:Fails.Count -eq 0)
}

# ------------------------------------------------------------------ POST CHECK (read-only) ----
function Invoke-PostCheck([string]$expectSha, [bool]$waitForWarm) {
    Log "PostCheck (expect code $expectSha)"
    $head = Invoke-Git rev-parse HEAD
    if ($head -ne $expectSha) { Fail "HEAD is $head" } else { Log 'HEAD matches' 'OK' }
    if ((Get-SvcStatus) -ne 'Running') { Fail "$Svc is not Running" } else { Log "$Svc Running" 'OK' }
    if (-not (Wait-Health 30)) { Fail 'health not ok' }
    if (-not (Assert-EnvGuard 'post')) { Fail 'environment guard failed after deployment' }
    if ($script:EnvSha -and (Sha256File $EnvFile) -ne $script:EnvSha) { Fail '.env changed during the run' }
    if (-not (Test-Path "$Dist\index.html")) { Fail 'frontend dist\index.html missing' }
    $base = $script:Baseline; if ($null -eq $base) { $base = [pscustomobject]@{ trades = $MinTrades; max_id = $MinMaxId; audit_rows = 0 } }
    $db = Read-Db
    if ($null -eq $db) { Warn 'post-deployment database read failed: trades/audit/warmer NOT VERIFIED' }
    else {
        Log "after: trades=$($db.trades) (baseline $($base.trades))  max_id=$($db.max_id) (baseline $($base.max_id))  audit_rows=$($db.audit_rows) (baseline $($base.audit_rows))"
        if ($db.trades -lt $base.trades -or $db.max_id -lt $base.max_id) { Fail 'demo trades fell below the baseline' } else { Log 'demo trades preserved' 'OK' }
        if (-not $db.audit_table) { Fail 'audit table missing' }
        elseif ($db.audit_rows -lt $base.audit_rows) { Fail 'audit rows fell below the baseline' } else { Log 'audit records preserved' 'OK' }
        $deadline = (Get-Date).AddSeconds(180)
        while ($waitForWarm -and $null -ne $db -and $null -eq $db.warm.plan_unlimited -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 20; $db = Read-Db }
        $w = $db.warm
        Log "warmer: status=$($w.status) complete=$($w.complete)/$($w.total) failed=$($w.failed) partial=$($w.partial) retrying=$($w.retrying) plan_unlimited=$($w.plan_unlimited) calls_limit=$($w.calls_limit) reason=$($w.pause_reason)"
        if ($null -eq $w.plan_unlimited) { Warn 'warmer has not saved a new-format state yet (not verified; recheck with -Mode PostCheck)' }
        elseif ($w.status -eq 'PAUSED_BUDGET') { Warn 'warmer still reports the old PAUSED_BUDGET state' }
        else { Log 'warmer runs under the unlimited plan (no daily cap reported)' 'OK' }
    }
    # authentication must still be enforced: an anonymous call to a protected route must be refused
    try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/api/warm/progress' -TimeoutSec 8; Fail "anonymous request to /api/warm/progress returned $($r.StatusCode): authentication looks bypassed" }
    catch { $code = 0; if ($_.Exception -is [System.Net.WebException] -and $null -ne $_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }; if ($code -in 401, 403) { Log "authentication still enforced (anonymous /api/warm/progress -> $code)" 'OK' } else { Warn "anonymous /api/warm/progress -> $code (could not confirm authentication)" } }
    # code-level UW constants (a fresh process importing the module; no network)
    Push-Location "$R\backend"
    try { $u = & $Py -c "import unusualwhales_service as u, tab_settings as t; print(u.MAX_INFLIGHT, t.UW_PLAN['unlimited_daily'])" 2>&1 | Select-Object -Last 1 } finally { Pop-Location }
    Log "UW client constants (MAX_INFLIGHT, unlimited_daily): $u"
    if ("$u" -ne '3 True') { Fail "unexpected UW constants: $u" }
    $tb = @(Select-String -Path $ErrLog -Pattern 'Traceback \(most recent call last\)').Count
    if ($script:TbBefore -ne $null -and $tb -gt $script:TbBefore) { Warn "error log gained $($tb - $script:TbBefore) Traceback(s) since the baseline - review" } else { Log 'no new Tracebacks in service.err.log' 'OK' }
    $bad = @(Get-Content -LiteralPath $ErrLog -Tail 400 | Select-String -Pattern 'QueuePool|ECHECKOUTTIMEOUT|UndefinedTable')
    if ($bad.Count -gt 0) { Warn "log tail has $($bad.Count) QueuePool/ECHECKOUTTIMEOUT/UndefinedTable line(s) - review (may pre-date the deployment)" }
    $st = Get-Content -LiteralPath $ErrLog -Tail 3000 | Select-String -Pattern 'signal_verification.stats' | Select-Object -Last 1
    if ($st) { Log "latest audit stats line: $($st.Line)" } else { Warn 'no audit stats line yet (appears periodically; recheck in a few minutes)' }
    if ((Invoke-Git status --porcelain)) { Warn 'working tree is no longer clean' }
    return ($script:Fails.Count -eq 0)
}

# ------------------------------------------------------------------ ROLLBACK (code + frontend only; the database is never touched) ----
function Invoke-Rollback([bool]$restoreDist) {
    Log 'ROLLBACK: restoring the original code (and frontend). The database is not touched.' 'WARN'
    try {
        if ((Get-SvcStatus) -ne 'Stopped') { Stop-Service-Safely }
        $br = $script:OrigBranch
        if ($br) { $null = Invoke-Git checkout $br } else { $null = Invoke-Git checkout --detach $Base }
        $now = Invoke-Git rev-parse HEAD
        if ($now -ne $Base) { Log "after checkout HEAD is $now" 'FAIL'; return $false }
        Log "code restored: HEAD $now $(if ($br) { '(branch ' + $br + ')' } else { '(detached)' })" 'OK'
        if ($restoreDist) { if (Restore-FrontendFromZip) { Log 'frontend restored from the exact ZIP and verified byte for byte' 'OK' } else { Log 'frontend restore did not verify' 'FAIL'; return $false } }
        Start-Service -Name $Svc -ErrorAction Stop
        if (Wait-Health $HealthWaitS) { Log 'ROLLBACK COMPLETE: service healthy on the original code' 'OK'; return $true }
        Log 'ROLLBACK DONE BUT HEALTH IS NOT OK: manual intervention required' 'FAIL'; return $false
    } catch { Log "ROLLBACK ERROR: $($_.Exception.Message) - manual intervention required" 'FAIL'; return $false }
}

# ------------------------------------------------------------------ DEPLOY ----
function Invoke-Deploy {
    if ($Confirm -ne 'DEPLOY 38ca61d') { Stop-Run "Deploy needs -Confirm 'DEPLOY 38ca61d'" }
    if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { Stop-Run 'run from an elevated PowerShell' }
    $lock = Join-Path $LogDir 'deploy.lock'
    if (Test-Path $lock) { Stop-Run "lock file exists ($lock): another run, or a previous run that needs review" }
    Set-Content -Path $lock -Value $Stamp
    $stage = 'PREFLIGHT'; $script:OrigBranch = ''
    try {
        if (-not (Invoke-Preflight)) { Stop-Run 'preflight failed: nothing was changed' }
        $script:OrigBranch = Invoke-Git branch --show-current
        $t0 = Get-Date
        @{ started = $Stamp; base = $Base; target = $Target; branch = $script:OrigBranch; trades = $script:Baseline.trades; max_id = $script:Baseline.max_id; audit_rows = $script:Baseline.audit_rows; env_sha = $script:EnvSha; tracebacks = $script:TbBefore } | ConvertTo-Json | Set-Content $StateFile
        $stage = 'STOPPING'; Stop-Service-Safely
        $stage = 'CODE'
        $null = Invoke-Git checkout --detach $Target
        if ($script:GitCode -ne 0 -or (Invoke-Git rev-parse HEAD) -ne $Target) { throw 'checkout of the target SHA failed' }
        Log "code switched: HEAD $Target" 'OK'
        foreach ($f in $Changed) {
            $o = & $Py -c "import ast,sys; ast.parse(open(sys.argv[1], encoding='utf-8').read())" "$R\backend\$($f.Split('\')[-1])" 2>&1
            if ($LASTEXITCODE -ne 0) { throw "syntax check failed for $f" }
        }
        Log 'syntax check of the 4 changed backend files ok' 'OK'
        $stage = 'BUILD'
        $outF = Join-Path $LogDir "npm_build_$Stamp.out.log"; $errF = Join-Path $LogDir "npm_build_$Stamp.err.log"
        $p = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run', 'build' -WorkingDirectory "$R\frontend" -RedirectStandardOutput $outF -RedirectStandardError $errF -PassThru -WindowStyle Hidden
        $null = $p.Handle     # PowerShell 5.1: without touching the handle, ExitCode comes back EMPTY even on success (reproduced; broke the first run)
        if (-not $p.WaitForExit($BuildTimeoutS * 1000)) { try { $p.Kill() } catch { }; throw "npm run build exceeded ${BuildTimeoutS}s" }
        $p.WaitForExit()
        $code = $p.ExitCode
        if ($null -eq $code) { throw "npm run build: the exit code is unavailable; treating as failure (see $outF and $errF)" }
        if ($code -ne 0) { throw "npm run build failed (exit $code); see $errF" }
        $idx = Get-Item -LiteralPath "$Dist\index.html" -ErrorAction Stop
        if ($idx.Length -lt 200 -or $idx.LastWriteTime -lt $t0) { throw 'dist\index.html is missing, tiny or not rebuilt' }
        $hit = Get-ChildItem "$Dist\assets" -Filter *.js -ErrorAction Stop | Select-String -Pattern 'partial data' -SimpleMatch -List | Select-Object -First 1
        if (-not $hit) { throw 'the built assets do not contain the new warm-up status text' }
        Log "frontend built: index.html $($idx.Length) bytes, new status text present" 'OK'
        $stage = 'STARTING'
        Start-Service -Name $Svc -ErrorAction Stop
        $stage = 'VERIFY'
        if (-not (Wait-Health $HealthWaitS)) { throw "health not ok within ${HealthWaitS}s" }
        if (-not (Invoke-PostCheck $Target $true)) { throw ("post-deployment check failed: " + ($script:Fails -join '; ')) }
        Log "DEPLOY COMPLETE: $Target is live. Warnings: $($script:Warns.Count)" 'OK'
        foreach ($w in $script:Warns) { Log "  review: $w" 'WARN' }
        Log 'Next: monitor 30 minutes (re-run -Mode PostCheck). Rollback, if needed: -Mode Rollback -Confirm ''ROLLBACK TO db6a72c'''
        return $true
    } catch {
        Log "FAILED at stage $stage : $($_.Exception.Message)" 'FAIL'
        if ($stage -eq 'PREFLIGHT') { return $false }                                   # nothing was changed
        $restoreDist = ($stage -in 'BUILD', 'STARTING', 'VERIFY')
        if ($stage -eq 'STOPPING') { try { Start-Service -Name $Svc -ErrorAction Stop; Wait-Health $HealthWaitS | Out-Null } catch { }; return $false }
        [void](Invoke-Rollback $restoreDist)
        return $false
    } finally { Remove-Item -Path $lock -Force -ErrorAction SilentlyContinue }
}

# ------------------------------------------------------------------ entry ----
Log "TRDGO.US UW-fix script  mode=$Mode  log=$LogFile"
$exit = 1
try {
    switch ($Mode) {
        'Preflight' { if (Invoke-Preflight) { Log 'PREFLIGHT PASS: nothing was changed' 'OK'; $exit = 0 } else { Log "PREFLIGHT FAIL ($($script:Fails.Count)): $($script:Fails -join '; ')" 'FAIL' } }
        'Deploy'    { if (Invoke-Deploy) { $exit = 0 } }
        'PostCheck' {
            if (Test-Path $StateFile) { $s = Get-Content $StateFile -Raw | ConvertFrom-Json; $script:Baseline = $s; $script:EnvSha = $s.env_sha; $script:TbBefore = $s.tracebacks }
            $head = Invoke-Git rev-parse HEAD
            if (Invoke-PostCheck $head $false) { Log "POSTCHECK PASS (warnings: $($script:Warns.Count))" 'OK'; $exit = 0 } else { Log "POSTCHECK FAIL: $($script:Fails -join '; ')" 'FAIL' }
        }
        'Rollback'  {
            if ($Confirm -ne 'ROLLBACK TO db6a72c') { Stop-Run "Rollback needs -Confirm 'ROLLBACK TO db6a72c'" }
            if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { Stop-Run 'run from an elevated PowerShell' }
            $script:OrigBranch = ''
            if (Test-Path $StateFile) { $script:OrigBranch = (Get-Content $StateFile -Raw | ConvertFrom-Json).branch }
            if (Invoke-Rollback $true) { $exit = 0 }
        }
    }
} catch { Log $_.Exception.Message 'FAIL' }
Log "exit $exit"
exit $exit
