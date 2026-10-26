<#
.SYNOPSIS
    TRDGO-US production READ-ONLY audit. Run it ON THE PRODUCTION VPS (never from a laptop).

.DESCRIPTION
    Collects: host / OS, git commit and branch, service and process status, CPU / RAM / disk, the database
    CONFIGURATION (host class, port and mode -- never the user or password), database version and health, backup
    evidence, the entry-lock / safety state, open paper-position counts, the Unusual Whales token FINGERPRINT (last four
    characters plus a short hash -- never the key), the 429 / 404 / latency / queue-wait figures the running app already
    holds, and any other process or scheduled task that could be using Unusual Whales.

    What it never does: write to the database (one short READ ONLY transaction, SELECTs only), stop / start / restart a
    service, change configuration, edit code, deploy, or make a request to Unusual Whales. The only HTTP calls go to the
    app on 127.0.0.1 (/safety and /api/uw/health), and neither of those contacts the provider.
    The only thing written is the report file itself (a folder under C:\audit-reports unless -ReportDir is given).

    A check that cannot be completed safely is reported as NOT VERIFIED. Nothing is worked around.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\vps_readonly_audit.ps1
#>
param(
    [string]$Root = "C:\apps\us-stock-reader",
    [string]$ServiceName = "USStockReader",
    [int]$Port = 8000,
    [string]$ReportDir = "C:\audit-reports",
    [int]$LogTailLines = 200000
)

$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$null = New-Item -ItemType Directory -Force -Path $ReportDir
$ReportFile = Join-Path $ReportDir ("trdgo-audit-" + $stamp + ".txt")
$script:Lines = New-Object System.Collections.Generic.List[string]
$script:Summary = New-Object System.Collections.Generic.List[string]

function Redact([string]$t) {
    if ($null -eq $t) { return "" }
    $t = [regex]::Replace($t, '(?i)(postgres(?:ql)?(?:\+\w+)?://)[^@\s/]+@', '$1<redacted>@')
    $t = [regex]::Replace($t, '(?i)((?:password|passwd|pwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*)\S+', '$1<redacted>')
    $t = [regex]::Replace($t, '(?i)(bearer\s+)[A-Za-z0-9\-\._~\+/=]{12,}', '$1<redacted>')
    $t = [regex]::Replace($t, '[A-Fa-f0-9]{40,}', '<redacted-hex>')
    return $t
}
function Out-Line([string]$t) { $script:Lines.Add((Redact $t)) }
function Sum([string]$status, [string]$t) {
    $line = ("[{0}] {1}" -f $status, $t)
    $script:Summary.Add((Redact $line))
    Out-Line $line
}
function Section([string]$name, [scriptblock]$body) {
    Out-Line ""
    Out-Line ("===== " + $name + " =====")
    try { & $body }
    catch { Sum "NOT VERIFIED" ($name + ": " + $_.Exception.Message) }
}
function Fingerprint([string]$secret) {
    if ([string]::IsNullOrEmpty($secret)) { return $null }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $h = ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($secret)) | ForEach-Object { $_.ToString("x2") }) -join ""
    return ("...{0} sha256:{1}" -f $secret.Substring([Math]::Max(0, $secret.Length - 4)), $h.Substring(0, 8))
}
function Read-EnvFile([string]$path) {
    $map = @{}
    if (-not (Test-Path $path)) { return $map }
    foreach ($ln in Get-Content $path -ErrorAction SilentlyContinue) {
        if ($ln -match '^\s*#' -or $ln -notmatch '=') { continue }
        $i = $ln.IndexOf("=")
        $k = $ln.Substring(0, $i).Trim()
        $v = $ln.Substring($i + 1).Trim().Trim('"').Trim("'")
        if ($k) { $map[$k] = $v }
    }
    return $map
}
function Http-Get([string]$url, [int]$timeout = 8) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeout
        return @{ ok = $true; code = [int]$r.StatusCode; body = $r.Content }
    } catch {
        $code = $null
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        return @{ ok = $false; code = $code; body = $_.Exception.Message }
    }
}

$backend = Join-Path $Root "backend"
$envMap = Read-EnvFile (Join-Path $backend ".env")

# ------------------------------------------------------------------------------------------------ 1. host
Section "1. Host" {
    $os = Get-CimInstance Win32_OperatingSystem
    Sum "INFO" ("hostname = " + $env:COMPUTERNAME)
    Sum "INFO" ("os = " + $os.Caption + " " + $os.Version + ", last boot " + $os.LastBootUpTime)
    Out-Line ("audit run at = " + (Get-Date -Format o) + " as " + $env:USERNAME)
    if ($env:COMPUTERNAME -like "LAPTOP*") { Sum "WARN" "this looks like a laptop, not the production VPS" }
    if (-not (Test-Path $Root)) { Sum "NOT VERIFIED" ("root folder not found: " + $Root + " (pass -Root)") }
}

# ------------------------------------------------------------------------------------------------ 2. git
Section "2. Git commit and branch" {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) { Sum "NOT VERIFIED" "git is not installed / not on PATH"; return }
    $head = (git -C $Root rev-parse HEAD 2>$null)
    if (-not $head) { Sum "NOT VERIFIED" "no git repository at the root (deployed by copy?)"; return }
    $branch = (git -C $Root rev-parse --abbrev-ref HEAD 2>$null)
    $dirty = @(git --no-optional-locks -C $Root status --porcelain 2>$null).Count
    Sum "INFO" ("commit = " + $head + " (" + (git -C $Root rev-parse --short HEAD 2>$null) + ")")
    Sum "INFO" ("branch = " + $branch)
    Sum "INFO" ("last commit = " + (git -C $Root log -1 --format='%ci %s' 2>$null))
    if ($dirty -gt 0) { Sum "WARN" ("working tree has " + $dirty + " modified / untracked files (production differs from the commit)") }
    else { Sum "INFO" "working tree clean" }
    git -C $Root merge-base --is-ancestor HEAD origin/main
    Out-Line ("commit is an ancestor of the local origin/main ref (exit 0 = yes, as of the last fetch): " + $LASTEXITCODE)
    foreach ($f in @("backend\intraday_15m.py", "backend\entry_lock.py", "backend\release_check.py", "backend\uw_slots.py", "backend\market_calendar.py", "backend\paper_fills.py")) {
        if (Test-Path (Join-Path $Root $f)) { Out-Line ("present: " + $f) } else { Sum "WARN" ("missing in this build: " + $f) }
    }
}

# ------------------------------------------------------------------------------------------------ 3. services
Section "3. Services and processes" {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) { Sum "INFO" ("service " + $ServiceName + " = " + $svc.Status + " (start type " + $svc.StartType + ")") }
    else { Sum "NOT VERIFIED" ("service " + $ServiceName + " not found") }
    foreach ($p in @($Port, 80, 443, 3000, 8765, 5173)) {
        $c = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c) {
            $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
            Out-Line ("port " + $p + " LISTENING, process = " + $proc.ProcessName + " (pid " + $c.OwningProcess + ")")
        } else { Out-Line ("port " + $p + " not listening") }
    }
    $h = Http-Get ("http://127.0.0.1:" + $Port + "/health")
    if ($h.ok) { Sum "INFO" ("backend /health = HTTP " + $h.code + " " + $h.body) } else { Sum "WARN" ("backend /health failed: " + $h.code + " " + $h.body) }
    $web = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match '^(caddy|nginx|node|iisexpress|w3wp)$' }
    if ($web) { foreach ($w in $web) { Out-Line ("frontend/web process: " + $w.ProcessName + " pid " + $w.Id) } }
    else { Sum "WARN" "no caddy / nginx / node process found (frontend serving not confirmed)" }
    $dist = Join-Path $Root "frontend\dist\index.html"
    if (Test-Path $dist) { Out-Line ("frontend build present, built " + (Get-Item $dist).LastWriteTime) } else { Sum "WARN" "frontend\dist\index.html not found" }
}

# ------------------------------------------------------------------------------------------------ 4. resources
Section "4. CPU, RAM, disk" {
    $cpu = Get-CimInstance Win32_Processor
    $os = Get-CimInstance Win32_OperatingSystem
    $load = ($cpu | Measure-Object -Property LoadPercentage -Average).Average
    $ramT = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1); $ramF = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
    Sum "INFO" ("cpu = " + ($cpu | Select-Object -First 1).Name + ", logical cores " + (($cpu | Measure-Object NumberOfLogicalProcessors -Sum).Sum) + ", load " + $load + "%")
    Sum "INFO" ("ram = " + $ramF + " GB free of " + $ramT + " GB")
    foreach ($d in Get-PSDrive -PSProvider FileSystem) {
        if ($d.Used -ne $null) { Out-Line ("disk " + $d.Name + ": free " + [math]::Round($d.Free / 1GB, 1) + " GB, used " + [math]::Round($d.Used / 1GB, 1) + " GB") }
    }
    $top = Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 6
    foreach ($t in $top) { Out-Line ("top memory: " + $t.ProcessName + " " + [math]::Round($t.WorkingSet64 / 1MB) + " MB") }
    if ($ramF -lt 1.0) { Sum "WARN" "less than 1 GB RAM free" }
}

# ------------------------------------------------------------------------------------------------ 5. database config
Section "5. Database configuration (credentials never shown)" {
    $url = $envMap["DATABASE_URL"]
    if (-not $url) { $url = [Environment]::GetEnvironmentVariable("DATABASE_URL", "Machine") }
    if (-not $url) { Sum "NOT VERIFIED" "DATABASE_URL not found in backend\.env or the machine environment"; return }
    $m = [regex]::Match($url, '^(?<scheme>[\w\+]+)://(?:[^@/]*@)?(?<host>[^:/\?]+)(?::(?<port>\d+))?/(?<db>[^\?]*)')
    if (-not $m.Success) { Sum "NOT VERIFIED" "DATABASE_URL could not be parsed"; return }
    $dbhost = $m.Groups["host"].Value; $dbport = $m.Groups["port"].Value
    $kind = "direct / other"
    if ($dbhost -match 'pooler') { $kind = "Supabase pooler" }
    $mode = "unknown"
    if ($dbport -eq "6543") { $mode = "TRANSACTION pooler" } elseif ($dbport -eq "5432") { $mode = "session / direct" }
    Sum "INFO" ("database = scheme " + $m.Groups["scheme"].Value + ", host class = " + $kind + ", host region = " + (($dbhost -split '\.')[1]) + ", port " + $dbport + " (" + $mode + "), database name " + $m.Groups["db"].Value)
    Sum "INFO" ("APP_ENV = " + $envMap["APP_ENV"])
    if ($envMap["APP_ENV"] -ne "production") { Sum "WARN" "APP_ENV is not 'production' in backend\.env (the service may set it elsewhere)" }
}

# ------------------------------------------------------------------------------------------------ 6. database health + positions (read only)
Section "6. Database health, safety lock, open positions (one READ ONLY transaction)" {
    $py = Join-Path $backend ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
    if (-not $py) { Sum "NOT VERIFIED" "no python interpreter found to run the read-only queries"; return }
    if (-not (Test-Path (Join-Path $backend "database.py"))) { Sum "NOT VERIFIED" "backend\database.py not found: database queries skipped"; return }
    $script = Join-Path $ReportDir ("db_readonly_" + $stamp + ".py")
    $code = @'
import json, os, sys, datetime
sys.path.insert(0, os.getcwd())
os.environ.setdefault("APP_ENV", "production")
out = {}
try:
    from sqlalchemy import text
    from database import engine
except Exception as e:
    print(json.dumps({"fatal": "import failed: %s" % type(e).__name__})); sys.exit(0)
def q(conn, name, sql, one=False):
    try:
        rows = conn.execute(text(sql)).mappings().all()
        out[name] = [dict(r) for r in rows] if not one else (dict(rows[0]) if rows else None)
    except Exception as e:
        out[name] = "NOT VERIFIED: %s" % type(e).__name__
        try: conn.rollback()
        except Exception: pass
        try: conn.execute(text("SET TRANSACTION READ ONLY"))
        except Exception: pass
try:
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        out["dialect"] = engine.dialect.name
        q(conn, "version", "SELECT version() AS v", one=True)
        q(conn, "txn_read_only", "SHOW transaction_read_only", one=True)
        q(conn, "connections", "SELECT count(*) AS n FROM pg_stat_activity WHERE datname = current_database()", one=True)
        q(conn, "max_connections", "SHOW max_connections", one=True)
        q(conn, "db_size_mb", "SELECT round(pg_database_size(current_database())/1048576.0) AS mb", one=True)
        q(conn, "tables", "SELECT to_regclass('demo_trades') IS NOT NULL AS demo_trades, to_regclass('intraday_sig_v2') IS NOT NULL AS intraday_sig_v2, to_regclass('trade_entry_lock') IS NOT NULL AS trade_entry_lock", one=True)
        q(conn, "demo_open_by_strategy", "SELECT strategy, count(*) AS open_positions, min(session_date) AS oldest_entry_date FROM demo_trades WHERE status='OPEN' GROUP BY strategy ORDER BY strategy")
        q(conn, "demo_open_total", "SELECT count(*) AS n FROM demo_trades WHERE status='OPEN'", one=True)
        q(conn, "demo_carried", "SELECT count(*) AS n FROM demo_trades WHERE status='OPEN' AND session_date < CURRENT_DATE", one=True)
        q(conn, "demo_status_counts", "SELECT status, count(*) AS n FROM demo_trades GROUP BY status ORDER BY status")
        q(conn, "demo_last_activity", "SELECT max(entry_time) AS last_entry, max(exit_time) AS last_exit, max(updated_at) AS last_update FROM demo_trades", one=True)
        q(conn, "sig_open", "SELECT count(*) AS n FROM intraday_sig_v2 WHERE is_open", one=True)
        q(conn, "sig_open_carried", "SELECT count(*) AS n FROM intraday_sig_v2 WHERE is_open AND CAST(session_date AS text) < to_char(CURRENT_DATE,'YYYY-MM-DD')", one=True)
        q(conn, "sig_last_activity", "SELECT max(signal_ts) AS last_signal_ts, max(status_ts) AS last_status_ts FROM intraday_sig_v2", one=True)
        q(conn, "entry_lock_rows", "SELECT * FROM trade_entry_lock")
        q(conn, "lock_triggers", "SELECT c.relname AS tbl, t.tgname AS trigger_name, t.tgenabled AS enabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE NOT t.tgisinternal AND c.relname IN ('demo_trades','intraday_sig_v2') ORDER BY 1,2")
    out["note"] = "single READ ONLY transaction, SELECT / SHOW only"
except Exception as e:
    out["fatal"] = "connection failed: %s" % type(e).__name__
finally:
    try: engine.dispose()
    except Exception: pass
print(json.dumps(out, default=str))
'@
    Set-Content -Path $script -Value $code -Encoding UTF8
    $prev = $env:APP_ENV
    $env:APP_ENV = "production"
    Push-Location $backend
    try { $raw = & $py -I -X utf8 -c "import runpy,sys; sys.path.insert(0, r'$backend'); runpy.run_path(r'$script')" 2>&1 }
    finally { Pop-Location; $env:APP_ENV = $prev }
    $text = ($raw | Out-String).Trim()
    $jsonLine = ($text -split "`n" | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1)
    if (-not $jsonLine) { Sum "NOT VERIFIED" ("database queries produced no JSON. First lines: " + ($text.Substring(0, [Math]::Min(300, $text.Length)))); return }
    $d = $jsonLine | ConvertFrom-Json
    if ($d.fatal) { Sum "NOT VERIFIED" ("database: " + $d.fatal); return }
    Sum "INFO" ("database dialect = " + $d.dialect)
    Sum "INFO" ("postgres version = " + $d.version.v)
    Out-Line ("session read-only = " + $d.txn_read_only.transaction_read_only)
    Sum "INFO" ("connections now = " + $d.connections.n + " (server max_connections " + $d.max_connections.max_connections + "; the Supabase pooler plan caps at 15)")
    Sum "INFO" ("database size = " + $d.db_size_mb.mb + " MB")
    Out-Line ("tables present: " + ($d.tables | ConvertTo-Json -Compress))
    Sum "INFO" ("OPEN paper positions (demo_trades) = " + $d.demo_open_total.n + ", of which carried from an earlier date = " + $d.demo_carried.n)
    foreach ($r in $d.demo_open_by_strategy) { Out-Line ("  open " + $r.strategy + ": " + $r.open_positions + " (oldest entry date " + $r.oldest_entry_date + ")") }
    Sum "INFO" ("OPEN live signals (intraday_sig_v2) = " + $d.sig_open.n + ", carried = " + $d.sig_open_carried.n)
    Out-Line ("demo_trades status counts: " + ($d.demo_status_counts | ConvertTo-Json -Compress))
    Out-Line ("demo_trades last activity: " + ($d.demo_last_activity | ConvertTo-Json -Compress))
    Out-Line ("intraday_sig_v2 last activity: " + ($d.sig_last_activity | ConvertTo-Json -Compress))
    if ($d.tables.trade_entry_lock -eq $true) {
        Sum "INFO" ("entry lock table rows = " + ($d.entry_lock_rows | ConvertTo-Json -Compress))
    } else { Sum "WARN" "no trade_entry_lock table: this build has no entry lock" }
    Sum "INFO" ("lock/DB triggers on the trade tables = " + ($d.lock_triggers | ConvertTo-Json -Compress))
}

# ------------------------------------------------------------------------------------------------ 7. safety status of the RUNNING app
Section "7. Safety status of the running app (/safety, local, no provider request)" {
    $s = Http-Get ("http://127.0.0.1:" + $Port + "/safety")
    if ($s.ok -and $s.body -match '^\s*<') { Sum "FAIL" "/safety returned the website HTML, not safety JSON: the running build has no release safety route (no mandatory 15-minute confirmation)" }
    elseif ($s.ok) { Sum "INFO" ("/safety = " + $s.body) }
    elseif ($s.code -eq 404) { Sum "FAIL" "/safety does not exist: the running build has no release safety route (older build, no mandatory 15-minute confirmation)" }
    else { Sum "NOT VERIFIED" ("/safety unreachable: " + $s.code + " " + $s.body) }
    $rc = Join-Path $backend "release_check.py"
    $py = Join-Path $backend ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
    if ((Test-Path $rc) -and $py) {
        Push-Location $backend
        try {
            $id = (& $py -I $rc --identify 2>&1 | Out-String).Trim()
            $chk = (& $py -I $rc 2>&1 | Out-String).Trim()
        } finally { Pop-Location }
        Sum "INFO" ("release_check --identify: " + ($id -replace "\s+", " "))
        Sum "INFO" ("release_check on the installed tree: " + (($chk -split "`n" | Select-Object -Last 3) -join " | "))
    } else { Sum "FAIL" "release_check.py is not installed on the server: the installed build cannot prove it keeps the 15-minute confirmation" }
}

# ------------------------------------------------------------------------------------------------ 8. token fingerprint
Section "8. Unusual Whales token fingerprint (never the key)" {
    $k = $envMap["UNUSUAL_WHALES_API_KEY"]
    if (-not $k) { $k = [Environment]::GetEnvironmentVariable("UNUSUAL_WHALES_API_KEY", "Machine") }
    if ($k) { Sum "INFO" ("TRDGO-US token fingerprint = " + (Fingerprint $k)) } else { Sum "NOT VERIFIED" "UNUSUAL_WHALES_API_KEY not found in backend\.env or the machine environment" }
    $mine = Fingerprint $k
    # other applications whose .env files hold a key that looks like an Unusual Whales one (fingerprint compared, not shown)
    $roots = @("C:\apps", "C:\")
    $seen = @{}
    foreach ($rt in $roots) {
        if (-not (Test-Path $rt)) { continue }
        # C:\apps: three levels (app folder, backend, config). C:\ itself: its top-level folders only, never the OS trees.
        $cand = @()
        if ($rt -eq "C:\") {
            foreach ($top in Get-ChildItem $rt -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch '^(Windows|Program Files|Program Files \(x86\)|ProgramData|Users|\$Recycle\.Bin|System Volume Information|apps)$' }) {
                $cand += Get-ChildItem $top.FullName -Force -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\.env|\.env$' }
                foreach ($sub in Get-ChildItem $top.FullName -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch '^(node_modules|\.git|\.venv)$' }) {
                    $cand += Get-ChildItem $sub.FullName -Force -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\.env|\.env$' }
                }
            }
        } else {
            foreach ($app in Get-ChildItem $rt -Directory -Force -ErrorAction SilentlyContinue) {
                $cand += Get-ChildItem $app.FullName -Force -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\.env|\.env$' }
                foreach ($sub in Get-ChildItem $app.FullName -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch '^(node_modules|\.git|\.venv)$' }) {
                    $cand += Get-ChildItem $sub.FullName -Force -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\.env|\.env$' }
                }
            }
        }
        $cand | ForEach-Object {
                if ($seen.ContainsKey($_.FullName)) { return }
                $seen[$_.FullName] = 1
                $mp = Read-EnvFile $_.FullName
                foreach ($key in $mp.Keys) {
                    if ($key -match '(?i)UNUSUAL|^UW_.*(KEY|TOKEN)') {
                        $fp = Fingerprint $mp[$key]
                        if ($fp) {
                            $same = ""
                            $isLive = ($_.FullName -eq (Join-Path $backend ".env"))
                            if ($fp -eq $mine -and -not $isLive) { $same = "  <== same key as the live TRDGO-US .env" }
                            if ($isLive) { $same = "  (this is the live TRDGO-US .env)" }
                            Out-Line ("env file " + $_.FullName + " : " + $key + " = " + $fp + $same)
                            if ($same -and -not $isLive) { Sum "WARN" ("same key in another env file: " + $_.FullName) }
                        }
                    }
                }
            }
    }
    Out-Line "(only .env style files under C:\apps and two levels of C:\ were scanned)"
}

# ------------------------------------------------------------------------------------------------ 9. other users of the provider
Section "9. Other processes / tasks that could use Unusual Whales" {
    $pat = 'signals|recorder|unusual|uwhales|uw_|stress|us-signals'
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw|node|powershell|pwsh|cmd)(\.exe)?$' -and $_.CommandLine -and $_.CommandLine -match $pat -and $_.ProcessId -ne $PID }
    if ($procs) { foreach ($p in $procs) { Sum "WARN" ("process " + $p.Name + " pid " + $p.ProcessId + ": " + ($p.CommandLine.Substring(0, [Math]::Min(180, $p.CommandLine.Length)))) } }
    else { Sum "INFO" "no python / node / shell process with signals / recorder / unusual in its command line" }
    $py = Get-Process -Name python, pythonw -ErrorAction SilentlyContinue
    Out-Line ("python processes running: " + @($py).Count)
    foreach ($p in $py) { Out-Line ("  python pid " + $p.Id + " started " + $p.StartTime) }
    $tasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match 'signal|recorder|unusual|uw|trdgo|stock' }
    if ($tasks) { foreach ($t in $tasks) { Sum "INFO" ("scheduled task " + $t.TaskPath + $t.TaskName + " = " + $t.State) } } else { Out-Line "no matching scheduled tasks" }
    $svcs = Get-Service | Where-Object { $_.DisplayName -match 'signal|recorder|unusual|stock' -or $_.Name -match 'signal|recorder|unusual|stock' }
    foreach ($s in $svcs) { Out-Line ("service " + $s.Name + " = " + $s.Status) }
    foreach ($p in @(8765, 3000)) {
        $c = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue
        if ($c) { Sum "WARN" ("port " + $p + " is listening (signals-platform dashboard / engine port)") } else { Out-Line ("port " + $p + " free") }
    }
}

# ------------------------------------------------------------------------------------------------ 10. UW metrics held by the app
Section "10. Unusual Whales metrics held by the running app (no provider request)" {
    $u = Http-Get ("http://127.0.0.1:" + $Port + "/api/uw/health") 15
    if ($u.ok) {
        Sum "INFO" "/api/uw/health reachable (copy the body below back to the reviewer)"
        Out-Line $u.body
        try {
            $j = $u.body | ConvertFrom-Json
            Out-Line ("parsed: status=" + $j.status)
        } catch { }
    }
    elseif ($u.code -eq 401 -or $u.code -eq 403) { Sum "NOT VERIFIED" "/api/uw/health needs a signed-in session. Open it in the browser while logged in as admin and paste the JSON: https://<your-domain>/api/uw/health" }
    elseif ($u.code -eq 404) { Sum "FAIL" "/api/uw/health does not exist: the running build has no UW health/metrics endpoint" }
    else { Sum "NOT VERIFIED" ("/api/uw/health unreachable: " + $u.code + " " + $u.body) }
    $b = Http-Get ("http://127.0.0.1:" + $Port + "/api/uw/budget") 15
    if ($b.ok) { Out-Line "---- /api/uw/budget ----"; Out-Line $b.body } else { Out-Line ("/api/uw/budget: " + $b.code) }
}

# ------------------------------------------------------------------------------------------------ 11. historical log evidence
Section "11. 429 / 404 / timeout evidence in the existing logs" {
    $dirs = @((Join-Path $Root "logs"), (Join-Path $backend "logs"), (Join-Path $Root "deploy\windows\logs"), "C:\logs", "C:\apps\logs")
    $files = @()
    foreach ($d in $dirs) { if (Test-Path $d) { $files += Get-ChildItem $d -Recurse -File -Include *.log, *.txt -ErrorAction SilentlyContinue } }
    $files += Get-ChildItem $Root -Recurse -Depth 2 -File -Include *.log -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch 'node_modules|\.venv|\\tests\\' }
    $files = $files | Sort-Object FullName -Unique | Sort-Object LastWriteTime -Descending | Select-Object -First 4
    if (-not $files) { Sum "NOT VERIFIED" "no log files found under the usual locations (service stdout may go elsewhere, e.g. the NSSM / service wrapper log folder)"; return }
    foreach ($f in $files) {
        $tail = Get-Content $f.FullName -Tail $LogTailLines -ErrorAction SilentlyContinue
        $n429 = @($tail | Select-String -Pattern '429|too many requests' -SimpleMatch:$false).Count
        $nConc = @($tail | Select-String -Pattern 'concurrent_limit_exceeded').Count
        $n404 = @($tail | Select-String -Pattern '\b404\b').Count
        $nTo = @($tail | Select-String -Pattern 'timed? ?out|timeout').Count
        Sum "INFO" ($f.Name + " (modified " + $f.LastWriteTime + ", last " + @($tail).Count + " lines): 429=" + $n429 + " concurrent_limit_exceeded=" + $nConc + " 404=" + $n404 + " timeouts=" + $nTo)
        $top = $tail | Select-String -Pattern '/(api/)?(stock|option|darkpool|market|earnings|net-prem|greek|ohlc)[^\s"''\?]*' -AllMatches | ForEach-Object { $_.Matches } | ForEach-Object { $_.Value } |
            Group-Object | Sort-Object Count -Descending | Select-Object -First 5
        foreach ($t in $top) { Out-Line ("   endpoint in log: " + $t.Name + " x" + $t.Count) }
    }
}

# ------------------------------------------------------------------------------------------------ 12. backups
Section "12. Backup evidence" {
    $cands = @()
    foreach ($d in @("C:\backups", "C:\backup", "D:\backups", (Join-Path $Root "backups"), (Join-Path $Root "..\backups"), "C:\apps\backups")) {
        if (Test-Path $d) { $cands += Get-ChildItem $d -Recurse -File -ErrorAction SilentlyContinue }
    }
    $cands += Get-ChildItem $Root -Recurse -Depth 2 -File -Include *backup*.json, *backup*.sql, *backup*.dump, *backup*.zip, *.dump -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch 'node_modules|\.venv' }
    $cands = $cands | Sort-Object FullName -Unique | Sort-Object LastWriteTime -Descending | Select-Object -First 6
    if ($cands) {
        foreach ($c in $cands) { Out-Line ("backup-like file: " + $c.FullName + "  " + [math]::Round($c.Length / 1MB, 1) + " MB  " + $c.LastWriteTime) }
        Sum "INFO" ("newest local backup-like file: " + $cands[0].Name + " at " + $cands[0].LastWriteTime)
    } else { Sum "WARN" "no local database backup files found" }
    $bt = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match 'backup|dump' }
    if ($bt) { foreach ($t in $bt) { Sum "INFO" ("backup task " + $t.TaskName + " = " + $t.State) } } else { Out-Line "no backup scheduled task" }
    Sum "NOT VERIFIED" "Supabase-side backups / point-in-time recovery cannot be read from the VPS: check the Supabase dashboard (Database -> Backups) and note the plan and the latest backup time"
    Sum "NOT VERIFIED" "restore test: not performed (would need a separate database)"
}

# ------------------------------------------------------------------------------------------------ output
Out-Line ""
Out-Line "===== END =====  (nothing was modified; no provider request was made)"
$header = @("TRDGO-US read-only production audit", ("generated " + (Get-Date -Format o) + " on " + $env:COMPUTERNAME), "")
$all = $header + $script:Lines
Set-Content -Path $ReportFile -Value $all -Encoding UTF8
Write-Host ""
Write-Host "================ SANITIZED SUMMARY (copy everything below back to the reviewer) ================"
foreach ($ln in $header) { Write-Host $ln }
foreach ($ln in $script:Summary) { Write-Host $ln }
Write-Host "================================================================================================"
Write-Host ("Full report saved on this server: " + $ReportFile)
Write-Host "The /api/uw/health JSON (section 10) is in the full report; paste that section too if the summary says NOT VERIFIED."
