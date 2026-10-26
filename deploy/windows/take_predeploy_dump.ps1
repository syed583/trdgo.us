param([string]$Root = "C:\apps\us-stock-reader",
      [string]$OutDir = "C:\audit-reports\db-backups",
      [string]$PgBin = "",
      [double]$TimeoutMin = 30,
      [double]$MinFractionOfPrevious = 0.9)
# TRDGO-US pre-deployment backup: PostgreSQL 17 pg_dump through the SESSION pooler (port 5432), custom format, public schema.
# READ-ONLY toward the database (pg_dump takes a consistent snapshot; it writes nothing there). Credentials come from
# backend\.env into environment variables of this process only; they are never put on a command line and never printed.
$ErrorActionPreference = "SilentlyContinue"; $Error.Clear()
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
function Redact([string]$t) {
    if ($null -eq $t) { return "" }
    $t = [regex]::Replace($t, '(?i)[a-z][a-z0-9+.\-]*://[^@\s/]+@', '<redacted-url>@')
    $t = [regex]::Replace($t, '(?i)((?:password|passwd|pwd|secret|token|api[_-]?key|PGPASSWORD|PGUSER|DATABASE_URL)\s*[=:]\s*)\S+', '$1<redacted>')
    return $t
}
function Say([string]$t) { Write-Host (Redact $t) }
function Fail([string]$why) {
    Say ("DUMP NOT VALIDATED: " + $why)
    foreach ($v in "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSSLMODE", "PGCONNECT_TIMEOUT", "PGAPPNAME") { Remove-Item ("Env:" + $v) -ErrorAction SilentlyContinue }
    exit 2
}
function Find-Tool([string]$name) {
    $c = @()
    if ($PgBin) { $c += (Join-Path $PgBin ($name + ".exe")) }
    $c += @((Get-Command $name -ErrorAction SilentlyContinue).Source)
    $c += @(("C:\pgtools17\pgsql\bin\" + $name + ".exe"), ("C:\pgsql\bin\" + $name + ".exe"), ("C:\Program Files\PostgreSQL\17\bin\" + $name + ".exe"))
    foreach ($dir in @(Get-ChildItem "C:\" -Directory -Filter "pg*" -ErrorAction SilentlyContinue)) { $c += (Join-Path $dir.FullName ("pgsql\bin\" + $name + ".exe")); $c += (Join-Path $dir.FullName ("bin\" + $name + ".exe")) }
    # the first candidate that is PostgreSQL 17 or newer (an older pg_dump earlier on PATH is skipped, never used)
    foreach ($t in @($c | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)) { if ((Major $t) -ge 17) { return $t } }
    return $null
}
function Major([string]$tool) { $v = "" + ((& $tool --version) -join " "); if ($v -match '(\d+)(?:\.\d+)*\s*$') { return [int]$matches[1] } else { return 0 } }

# ---- 1. tools: PostgreSQL 17 or newer, both of them -------------------------------------------------------------
$dump = Find-Tool "pg_dump"; $restore = Find-Tool "pg_restore"
if (-not $dump) { Fail "a PostgreSQL 17 (or newer) pg_dump was not found; the server is PostgreSQL 17 (pass -PgBin <folder with pg_dump.exe>)" }
if (-not $restore) { Fail "a PostgreSQL 17 (or newer) pg_restore was not found (needed to read the dump back; pass -PgBin)" }
Say ("tools: " + (("" + ((& $dump --version) -join " "))) + " / " + (("" + ((& $restore --version) -join " "))))

# ---- 2. connection settings from backend\.env (never printed) -------------------------------------------------------
$line = (Get-Content (Join-Path $Root "backend\.env") -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -First 1)
if (-not $line) { Fail "DATABASE_URL was not found in backend\.env" }
$u = ("" + $line) -replace '^\s*DATABASE_URL\s*=\s*', ''; $u = $u.Trim().Trim([char]34, [char]39)
$m = [regex]::Match($u, '^[\w\+]+://(?<user>[^:]+):(?<pw>.+)@(?<host>[^@/:]+)(?::\d+)?/(?<db>[^?]+)')
if (-not $m.Success) { Fail "DATABASE_URL could not be parsed" }
if ($m.Groups["host"].Value -notmatch 'pooler') { Fail "the database host is not a Supabase pooler host: refusing to guess a session connection" }

# ---- 3. destination: new file only, enough space ------------------------------------------------------------------
$null = New-Item -ItemType Directory -Force -Path $OutDir
$file = Join-Path $OutDir ("trdgo_us_" + $stamp + "_predeploy.dump")
if (Test-Path $file) { Fail ("the file already exists, nothing is overwritten: " + $file) }
$OutDir = (Resolve-Path $OutDir).Path
$freeBytes = $null; try { $freeBytes = (New-Object System.IO.DriveInfo ([System.IO.Path]::GetPathRoot($OutDir))).AvailableFreeSpace } catch { }
if ($null -eq $freeBytes) { Fail "the free space of the backup drive could not be read" }
$freeGB = [math]::Round($freeBytes / 1GB, 1)
if ($freeGB -lt 1.0) { Fail ("only " + $freeGB + " GB free on the backup drive") }
$previous = @(Get-ChildItem $OutDir -Filter *.dump -File -ErrorAction SilentlyContinue | Sort-Object Length -Descending | Select-Object -First 1)
$etNow = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, "Eastern Standard Time")
Say ("start " + (Get-Date -Format o) + "; ET " + $etNow.ToString("ddd HH:mm") + "; target " + $file + "; free " + $freeGB + " GB")

# ---- 4. the dump (session pooler, port 5432). The values live only in this process's environment. ------------------
$env:PGHOST = $m.Groups["host"].Value; $env:PGPORT = "5432"
$env:PGUSER = [uri]::UnescapeDataString($m.Groups["user"].Value); $env:PGPASSWORD = [uri]::UnescapeDataString($m.Groups["pw"].Value)
$env:PGDATABASE = $m.Groups["db"].Value; $env:PGSSLMODE = "require"; $env:PGCONNECT_TIMEOUT = "15"; $env:PGAPPNAME = "trdgo-predeploy-dump"
$log = $file + ".log"; $sink = $file + ".out"
$exit = $null; $timedOut = $false
try {
    $p = Start-Process -FilePath $dump -ArgumentList ('-Fc -Z 6 --no-owner --no-privileges --schema=public --lock-wait-timeout=60s --verbose -f "' + $file + '"') -NoNewWindow -PassThru -RedirectStandardError $log -RedirectStandardOutput $sink
    $null = $p.Handle                                   # Windows PowerShell 5.1 loses ExitCode unless the handle is cached
    if (-not $p.WaitForExit([int]($TimeoutMin * 60000))) { try { $p.Kill() } catch { }; $timedOut = $true } else { $p.WaitForExit(); $exit = $p.ExitCode }
} finally {
    foreach ($v in "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSSLMODE", "PGCONNECT_TIMEOUT", "PGAPPNAME") { Remove-Item ("Env:" + $v) -ErrorAction SilentlyContinue }
    $u = $null; $m = $null; $line = $null
}
Remove-Item $sink -ErrorAction SilentlyContinue
if ($timedOut) { if (Test-Path $file) { Rename-Item $file ($file + ".failed") }; Fail ("pg_dump did not finish within " + $TimeoutMin + " minutes and was stopped (partial file kept as .failed; log: " + $log + ")") }
if ($null -eq $exit -or $exit -ne 0) { if (Test-Path $file) { Rename-Item $file ($file + ".failed") }; Fail ("pg_dump did not report a successful exit (exit code: " + $(if ($null -eq $exit) { "unknown" } else { $exit }) + "; partial file kept as .failed; log: " + $log + ")") }
if (-not (Test-Path $file)) { Fail "pg_dump reported success but wrote no file" }

# ---- 5. validation ------------------------------------------------------------------------------------------------
$f = Get-Item $file
if ($f.Length -lt 1MB) { Fail ("the dump is only " + $f.Length + " bytes") }
if ($previous.Count -gt 0 -and $f.Length -lt ($previous[0].Length * $MinFractionOfPrevious)) { Fail ("the dump (" + [math]::Round($f.Length / 1MB, 1) + " MB) is much smaller than the largest earlier dump (" + [math]::Round($previous[0].Length / 1MB, 1) + " MB): not accepted") }
$len = $f.Length; Start-Sleep -Seconds 3; $f.Refresh()
if ($f.Length -ne $len) { Fail "the file is still changing" }
$toc = @(& $restore --list $file 2>$null); $code = $LASTEXITCODE
$entries = @($toc | Where-Object { $_ -and $_ -notmatch '^;' })
if ($code -ne 0 -or $entries.Count -lt 100) { Fail ("pg_restore --list failed or read too few entries (exit " + $code + ", " + $entries.Count + ")") }
$dataTables = @($entries | ForEach-Object { $mm = [regex]::Match($_, 'TABLE DATA public (\S+)'); if ($mm.Success) { $mm.Groups[1].Value } })
$need = @("demo_trades", "intraday_sig_v2", "trade_calls", "model_predictions", "earnings_signal_snapshots", "ped_scores")
$missing = @($need | Where-Object { $dataTables -notcontains $_ })
if ($dataTables.Count -lt 50) { Fail ("only " + $dataTables.Count + " tables have data entries (55 public tables are expected)") }
if ($missing.Count -gt 0) { Fail ("the dump has no data entry for: " + ($missing -join ", ")) }
$sha = (Get-FileHash $file -Algorithm SHA256).Hash
Set-Content -Path ($file + ".sha256") -Value ($sha + "  " + $f.Name) -Encoding ASCII
Start-Sleep -Seconds 2
$sha2 = (Get-FileHash $file -Algorithm SHA256).Hash
if ($sha -ne $sha2) { Fail "the SHA-256 changed between two reads" }
$verify = ((Get-Content ($file + ".sha256") -First 1) -split '\s+')[0]
if ($verify -ne (Get-FileHash $file -Algorithm SHA256).Hash) { Fail "the SHA-256 file does not match the dump" }
$manifest = @("file: " + $f.Name, "bytes: " + $f.Length, "sha256: " + $sha, "written: " + $f.LastWriteTime.ToString("o"),
              "pg_dump: " + (("" + ((& $dump --version) -join " "))), "toc_entries: " + $entries.Count, "tables_with_data: " + $dataTables.Count,
              "required_tables_present: " + ($need -join ","), "connection: session pooler, port 5432, schema public, custom format -Z 6",
              "restore_test: NOT repeated (the same procedure was restore-tested on 2026-10-26)")
Set-Content -Path ($file + ".manifest.txt") -Value $manifest -Encoding UTF8
Say ""
Say ("DUMP VALIDATED: " + $f.Name)
Say ("  size      " + [math]::Round($f.Length / 1MB, 1) + " MB (" + $f.Length + " bytes)")
Say ("  sha256    " + $sha + "  (also in " + $f.Name + ".sha256)")
Say ("  contents  " + $entries.Count + " archive entries, " + $dataTables.Count + " tables with data; all " + $need.Count + " key tables present")
Say ("  written   " + $f.LastWriteTime.ToString("o") + "  (the preflight accepts it for 60 minutes)")
Say ("  pg_restore used: " + $restore)
Say "Next: rerun the approved preflight (it will validate this dump itself)."
exit 0
