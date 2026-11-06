# TRDGO-US: READ-ONLY verification that the paper book is flat after the 16:00 ET close (and stays flat after 20:00 ET).
# Every query runs in its own READ ONLY transaction that is rolled back. Aggregates only; no connection string, key or token is read or printed.
# Writes ONE new file: after_close_check_<stamp>.py in $ReportDir. Changes no database row, no git state, no service.
# Exit code: 0 = PASS (final), 1 = FAIL, 3 = WAIT (too early / not final yet), 2 = could not run. Anything but 0 is NOT a GO.
param([string]$Root = "C:\apps\us-stock-reader", [string]$ReportDir = "C:\audit-reports", [string]$AppEnv = "production", [int]$TimeoutSec = 120,
      [string]$AsOfEt = "")          # AsOfEt is for rehearsal only: it overrides the ET clock used to pick the phase, e.g. "2026-10-27 21:00"
$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backend = Join-Path $Root "backend"; $py = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "STOPPED: no python at $py" -ForegroundColor Red; exit 2 }
$null = New-Item -ItemType Directory -Force -Path $ReportDir
$prog = Join-Path $ReportDir ("after_close_check_" + $stamp + ".py")
@'
import os, sys, json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0, os.environ["TRDGO_BACKEND"])
ET = ZoneInfo("America/New_York")
from sqlalchemy import text
from database import engine
def q(c, sql, **p):
    s = sql.strip()
    if not s.upper().startswith(("SELECT", "SHOW")) or ";" in s: raise ValueError("refused: not a single read-only statement")
    try:
        c.execute(text("SET TRANSACTION READ ONLY"))
        if str(c.execute(text("SHOW transaction_read_only")).scalar()).lower() != "on": raise RuntimeError("not read-only")
        return [dict(r) for r in c.execute(text(s), p).mappings().all()]
    finally:
        try: c.rollback()
        except Exception: pass
asof = os.environ.get("TRDGO_ASOF_ET", "").strip()
now = datetime.strptime(asof, "%Y-%m-%d %H:%M").replace(tzinfo=ET) if asof else datetime.now(ET)
today = now.date().isoformat()
t = now.hour * 60 + now.minute
weekend = now.weekday() >= 5
res = []                    # (status, text)
def add(s, tx): res.append((s, tx)); print(f"[{s}] {tx}")
print("ET clock used:", now.strftime("%a %Y-%m-%d %H:%M"), "(rehearsal override)" if asof else "")
phase = "FINAL" if (weekend or t >= 20 * 60 or t < 4 * 60) else ("WAIT" if t < 16 * 60 + 3 else "AFTER_CLOSE")
print("phase:", {"WAIT": "before 16:03 ET - too early to judge the close", "AFTER_CLOSE": "16:03-20:00 ET - the close has happened, entries are still permitted until 20:00", "FINAL": "outside 04:00-20:00 ET or a weekend - no entry is possible"}[phase])
with engine.connect() as c:
    dbnow = q(c, "SELECT now() AS n")[0]["n"]
    skew = abs((datetime.now(ET) - dbnow.astimezone(ET)).total_seconds()) if dbnow else None
    add("PASS" if skew is not None and skew < 120 else "FAIL", f"VPS clock vs database clock differ by {skew:.0f} s" if skew is not None else "database clock unreadable")
    st = {x["status"]: x["n"] for x in q(c, "SELECT status, count(*) AS n FROM demo_trades GROUP BY status")}
    openn = int(st.get("OPEN", 0))
    pre = int(q(c, "SELECT count(*) AS n FROM demo_trades WHERE status='OPEN' AND (entry_time AT TIME ZONE 'America/New_York')::time < time '16:00' AND session_date <= CAST(:t AS date)", t=today)[0]["n"])
    post = int(q(c, "SELECT count(*) AS n FROM demo_trades WHERE status='OPEN' AND (entry_time AT TIME ZONE 'America/New_York')::time >= time '16:00'")[0]["n"])
    carried = int(q(c, "SELECT count(*) AS n FROM demo_trades WHERE status='OPEN' AND session_date < CAST(:t AS date)", t=today)[0]["n"])
    stuck = int(q(c, "SELECT count(*) AS n FROM demo_trades WHERE status='OPEN' AND entry_time < now() - interval '30 hours'")[0]["n"])
    act = q(c, "SELECT max(entry_time) AS last_entry, max(exit_time) AS last_exit, count(*) FILTER (WHERE (entry_time AT TIME ZONE 'America/New_York')::date = CAST(:t AS date) AND (entry_time AT TIME ZONE 'America/New_York')::time >= time '16:00') AS entries_after_close, count(*) FILTER (WHERE (entry_time AT TIME ZONE 'America/New_York')::date = CAST(:t AS date) AND (entry_time AT TIME ZONE 'America/New_York')::time >= time '20:00') AS entries_after_2000, count(*) FILTER (WHERE entry_time > now() - interval '15 minutes') AS entries_15m, count(*) FILTER (WHERE status='OPEN' AND updated_at > now() - interval '5 minutes') AS open_refreshed_5m, count(*) FILTER (WHERE status='TIMED_EXIT' AND (exit_time AT TIME ZONE 'America/New_York')::date = CAST(:t AS date)) AS timed_today, min(exit_time) FILTER (WHERE status='TIMED_EXIT' AND (exit_time AT TIME ZONE 'America/New_York')::date = CAST(:t AS date)) AS first_timed, max(exit_time) FILTER (WHERE status='TIMED_EXIT' AND (exit_time AT TIME ZONE 'America/New_York')::date = CAST(:t AS date)) AS last_timed FROM demo_trades", t=today)[0]
    def e(v): return "-" if v is None else v.astimezone(ET).strftime("%m-%d %H:%M:%S")
    print(f"status counts: {dict(sorted(st.items()))}")
    print(f"OPEN now: {openn} | opened before 16:00: {pre} | opened after 16:00: {post} | carried from an earlier session: {carried} | older than 30 h: {stuck}")
    print(f"last entry {e(act['last_entry'])} ET | last exit {e(act['last_exit'])} ET | TIMED_EXIT today: {act['timed_today']} ({e(act['first_timed'])} .. {e(act['last_timed'])} ET)")
    print(f"entries since 16:00 ET today: {act['entries_after_close']} | entries since 20:00 ET today: {act['entries_after_2000']} | entries in last 15 min: {act['entries_15m']} | OPEN rows refreshed in last 5 min: {act['open_refreshed_5m']}")
    if post:
        print("OPEN positions opened after 16:00 ET (the production build does not close these on the same day):")
        for x in q(c, "SELECT strategy, symbol, side, session_date, entry_time FROM demo_trades WHERE status='OPEN' AND (entry_time AT TIME ZONE 'America/New_York')::time >= time '16:00' ORDER BY entry_time LIMIT 40"):
            print(f"   {x['strategy']:<10} {x['symbol']:<8} {x['side']:<6} session {x['session_date']}  in {e(x['entry_time'])} ET")
    # ---- judgement ---------------------------------------------------------------------------------------------
    if phase == "WAIT":
        add("WAIT", "too early: run this after 16:03 ET (the tracker closes on its first tick after 16:00; it loops every 60 s)")
    else:
        if phase == "AFTER_CLOSE" or phase == "FINAL":
            add("PASS" if pre == 0 else "FAIL", f"positions opened before 16:00 and still OPEN: {pre} (must be 0 once the close has passed)")
        add("PASS" if carried == 0 else "FAIL", f"OPEN positions carried from an earlier session: {carried} (must be 0)")
        add("PASS" if stuck == 0 else "FAIL", f"stale OPEN positions older than 30 h: {stuck} (must be 0)")
        if phase == "AFTER_CLOSE":
            add("INFO", f"entries since 16:00 ET: {act['entries_after_close']} - the production build may still open positions until 20:00 ET, so 'no new entries' is judged after 20:00")
            add("INFO", f"OPEN positions opened after 16:00 ET: {post} - these are not closed by the production build; the book is only flat if this is 0 at 20:00")
        if phase == "FINAL":
            add("PASS" if openn == 0 else "FAIL", f"OPEN paper positions in total: {openn} (must be 0)")
            add("PASS" if int(act["entries_after_2000"] or 0) == 0 else "FAIL", f"entries created since 20:00 ET today: {act['entries_after_2000']} (must be 0)") if not weekend and t >= 20 * 60 else add("INFO", "entry-after-20:00 test applies only after 20:00 ET on a weekday")
try: engine.dispose()
except Exception: pass
bad = [x for x in res if x[0] == "FAIL"]
if bad: verdict, code = "FAIL", 1
elif phase == "WAIT": verdict, code = "WAIT", 3
elif phase == "AFTER_CLOSE": verdict, code = "WAIT", 3          # not final until 20:00 ET
else: verdict, code = "PASS", 0
print(); print("VERDICT:", verdict, "(" + {"PASS": "the paper book is flat and stayed flat", "FAIL": "do not proceed", "WAIT": "not final: run again after 20:00 ET"}[verdict] + ")")
print("EXITCODE:", code)
sys.exit(code)
'@ | Set-Content -Path $prog -Encoding UTF8
Write-Host ("query program written (new file): " + $prog)

# can the tracker run at all? (names only: DISABLE_WARMERS switches the background tracker off in the production build)
$dw = @(); foreach ($sc in "Machine", "User", "Process") { if ([Environment]::GetEnvironmentVariable("DISABLE_WARMERS", $sc)) { $dw += ($sc + " environment") } }
$envFile = Join-Path $backend ".env"; if (Test-Path $envFile) { if (Select-String -Path $envFile -Pattern '^\s*DISABLE_WARMERS\s*=\s*\S' -Quiet) { $dw += ".env" } }
if ($dw.Count) { Write-Host ("WARNING: DISABLE_WARMERS is set in: " + ($dw -join ", ") + " - the production build would NOT run the paper tracker") -ForegroundColor Red } else { Write-Host "[PASS] DISABLE_WARMERS is not set anywhere (machine, user, .env): the paper tracker is enabled" }

$so = Join-Path $ReportDir ("after_close_check_" + $stamp + ".tmp"); $se = Join-Path $ReportDir ("after_close_check_err_" + $stamp + ".tmp")
$env:APP_ENV = $AppEnv; $env:TRDGO_BACKEND = $backend; $env:PYTHONDONTWRITEBYTECODE = "1"; if ($AsOfEt) { $env:TRDGO_ASOF_ET = $AsOfEt }
$p = $null
try {
    $p = Start-Process -FilePath $py -ArgumentList ('-I -X utf8 "' + $prog + '"') -WorkingDirectory $backend -NoNewWindow -PassThru -RedirectStandardOutput $so -RedirectStandardError $se
    if (-not $p.WaitForExit($TimeoutSec * 1000)) { try { $p.Kill() } catch { }; Write-Host ("timed out after " + $TimeoutSec + " s and was stopped") -ForegroundColor Red }
} finally { Remove-Item Env:APP_ENV, Env:TRDGO_BACKEND, Env:PYTHONDONTWRITEBYTECODE, Env:TRDGO_ASOF_ET -ErrorAction SilentlyContinue }
$text = if (Test-Path $so) { Get-Content $so -Raw } else { "" }
$text = [regex]::Replace($text, '(?i)[a-z][a-z0-9+.\-]*://[^@\s/]+@', '<redacted-url>@')
$text = [regex]::Replace($text, '[A-Za-z0-9+/=_\-]{40,}', '<redacted>')
Write-Host $text
# the verdict comes from the program's own EXITCODE line; the process exit code (which Windows PowerShell 5.1 can lose) must agree when it is known
$parsed = $null; if ($text -match 'EXITCODE:\s*(\d+)') { $parsed = [int]$Matches[1] }
$proc = $null; if ($p) { $null = $p.Handle; $p.WaitForExit(); if ($null -ne $p.ExitCode) { $proc = [int]$p.ExitCode } }
$code = 2
if ($null -ne $parsed -and ($text -match 'VERDICT:') -and ($null -eq $proc -or $proc -eq $parsed)) { $code = $parsed }
else { Write-Host ("no usable verdict (output code " + $parsed + ", process code " + $proc + "; the error stream is withheld): treat as NOT PASSED") -ForegroundColor Yellow }
Remove-Item $so, $se -ErrorAction SilentlyContinue
Write-Host ("script exit code: " + $code + "   (0 = PASS only; 1 = FAIL; 3 = WAIT; 2 = could not run)")
exit $code
