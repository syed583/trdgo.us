# TRDGO-US: READ-ONLY investigation of the open paper positions (demo_trades). Every query runs in its own READ ONLY transaction that is
# rolled back, never committed (same mechanism as the corrected preflight). Aggregates only: no connection string, key or token is read or printed.
# Writes ONE new file: paper_investigation_<stamp>.py (the query program) in $ReportDir. Changes no database row, no git state, no service.
param([string]$Root = "C:\apps\us-stock-reader", [string]$ReportDir = "C:\audit-reports", [string]$AppEnv = "production", [int]$TimeoutSec = 120)
$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backend = Join-Path $Root "backend"
$py = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "STOPPED: no python at $py" -ForegroundColor Red; exit 2 }
$null = New-Item -ItemType Directory -Force -Path $ReportDir
$prog = Join-Path $ReportDir ("paper_investigation_" + $stamp + ".py")
@'
import os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, os.environ["TRDGO_BACKEND"])
ET = ZoneInfo("America/New_York")
from sqlalchemy import text
from database import engine
def et(v):
    if v is None: return "-"
    try: return v.astimezone(ET).strftime("%m-%d %H:%M")
    except Exception: return str(v)
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
def sect(t): print(); print("=== " + t)
now = datetime.now(ET)
print("ET now:", now.strftime("%a %Y-%m-%d %H:%M"), "| today ET:", now.date())
today = now.date().isoformat()
with engine.connect() as c:
    sect("1. demo_trades by status (all history)")
    for r in q(c, "SELECT status, count(*) AS n FROM demo_trades GROUP BY status ORDER BY n DESC"): print(f"  {r['status']:<12} {r['n']}")
    sect("2. OPEN positions by strategy / side / session_date  (entry times in ET)")
    for r in q(c, "SELECT strategy, side, session_date, count(*) AS n, min(entry_time) AS first_in, max(entry_time) AS last_in FROM demo_trades WHERE status='OPEN' GROUP BY strategy, side, session_date ORDER BY session_date, strategy, side"):
        print(f"  {r['strategy']:<10} {r['side']:<6} session {r['session_date']}  n={r['n']:<4} first {et(r['first_in'])}  last {et(r['last_in'])}")
    sect("3. OPEN positions: carried from an earlier session_date vs today (ET)")
    for r in q(c, "SELECT (session_date < CAST(:t AS date)) AS carried, count(*) AS n FROM demo_trades WHERE status='OPEN' GROUP BY 1 ORDER BY 1", t=today): print(f"  carried={r['carried']}  n={r['n']}")
    sect("4. OPEN positions by hour of entry (ET)")
    for r in q(c, "SELECT date_trunc('hour', entry_time AT TIME ZONE 'America/New_York') AS h, count(*) AS n FROM demo_trades WHERE status='OPEN' GROUP BY 1 ORDER BY 1"): print(f"  {str(r['h'])[:16]}  {r['n']}")
    sect("5. OPEN positions entered AFTER the 16:00 ET close (the old build never closes these on the same day)")
    for r in q(c, "SELECT session_date, count(*) AS n FROM demo_trades WHERE status='OPEN' AND (entry_time AT TIME ZONE 'America/New_York')::time >= time '16:00' GROUP BY 1 ORDER BY 1"): print(f"  session {r['session_date']}  n={r['n']}")
    sect("6. OPEN positions by symbol (count; strategies)")
    for r in q(c, "SELECT symbol, count(*) AS n, string_agg(DISTINCT strategy, ',') AS strategies FROM demo_trades WHERE status='OPEN' GROUP BY symbol ORDER BY n DESC, symbol"): print(f"  {r['symbol']:<8} {r['n']:<3} {r['strategies']}")
    sect("7. is the tracker alive and still creating positions?")
    for r in q(c, "SELECT now() AS db_now, max(entry_time) AS last_entry, max(exit_time) AS last_exit, count(*) FILTER (WHERE entry_time > now() - interval '15 minutes') AS entries_15m, count(*) FILTER (WHERE entry_time > now() - interval '60 minutes') AS entries_60m, count(*) FILTER (WHERE exit_time > now() - interval '60 minutes') AS exits_60m, count(*) FILTER (WHERE status='OPEN' AND updated_at > now() - interval '5 minutes') AS open_refreshed_5m FROM demo_trades"):
        print(f"  db now {et(r['db_now'])} ET | last entry {et(r['last_entry'])} | last exit {et(r['last_exit'])}")
        print(f"  entries last 15 min: {r['entries_15m']} | entries last 60 min: {r['entries_60m']} | exits last 60 min: {r['exits_60m']} | open rows refreshed in last 5 min: {r['open_refreshed_5m']}")
    sect("8. how positions normally end: the last 6 sessions (opened / still OPEN / closed by status / typical close time ET)")
    for r in q(c, "SELECT session_date, count(*) AS opened, count(*) FILTER (WHERE status='OPEN') AS still_open, count(*) FILTER (WHERE status='STOP') AS stop, count(*) FILTER (WHERE status='TARGET') AS target, count(*) FILTER (WHERE status='SCORE_EXIT') AS score_exit, count(*) FILTER (WHERE status='TIMED_EXIT') AS timed, count(*) FILTER (WHERE status NOT IN ('OPEN','STOP','TARGET','SCORE_EXIT','TIMED_EXIT')) AS other, min(exit_time) AS first_exit, max(exit_time) AS last_exit FROM demo_trades GROUP BY session_date ORDER BY session_date DESC LIMIT 6"):
        print(f"  {r['session_date']}  opened {r['opened']:<4} open {r['still_open']:<4} stop {r['stop']:<3} target {r['target']:<3} score {r['score_exit']:<3} timed {r['timed']:<4} other {r['other']:<2} exits {et(r['first_exit'])} .. {et(r['last_exit'])}")
    sect("9. OPEN positions: data quality")
    for r in q(c, "SELECT count(*) AS n, count(*) FILTER (WHERE stop IS NULL OR target IS NULL) AS no_levels, count(*) FILTER (WHERE last_price IS NULL) AS no_price, round(sum(profit)::numeric, 2) AS unrealised_sum, count(DISTINCT symbol) AS symbols, count(DISTINCT strategy) AS strategies, min(shares) AS min_shares, max(shares) AS max_shares FROM demo_trades WHERE status='OPEN'"): print("  " + ", ".join(f"{k}={v}" for k, v in r.items()))
    sect("10. does the database hold anything broker- or order-shaped?")
    cols = q(c, "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='demo_trades' ORDER BY ordinal_position")
    print("  demo_trades columns:", ", ".join(x["column_name"] for x in cols))
    tabs = q(c, "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name ~* '(order|broker|fill|execution|account|brokerage)' ORDER BY 1")
    print("  tables with order/broker/fill/execution/account in the name:", ", ".join(x["table_name"] for x in tabs) or "(none)")
    sect("11. entry lock (new build only)")
    print("  trade_entry_lock present:", q(c, "SELECT to_regclass('trade_entry_lock') IS NOT NULL AS present")[0]["present"])
try: engine.dispose()
except Exception: pass
'@ | Set-Content -Path $prog -Encoding UTF8

Write-Host ("query program written (new file): " + $prog)
$so = Join-Path $ReportDir ("paper_investigation_" + $stamp + ".tmp"); $se = Join-Path $ReportDir ("paper_investigation_err_" + $stamp + ".tmp")
$env:APP_ENV = $AppEnv; $env:TRDGO_BACKEND = $backend; $env:PYTHONDONTWRITEBYTECODE = "1"
try {
    $p = Start-Process -FilePath $py -ArgumentList ('-I -X utf8 "' + $prog + '"') -WorkingDirectory $backend -NoNewWindow -PassThru -RedirectStandardOutput $so -RedirectStandardError $se
    if (-not $p.WaitForExit($TimeoutSec * 1000)) { try { $p.Kill() } catch { }; Write-Host ("the query program timed out after " + $TimeoutSec + " s and was stopped") -ForegroundColor Red }
} finally { Remove-Item Env:APP_ENV, Env:TRDGO_BACKEND, Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue }
$text = if (Test-Path $so) { Get-Content $so -Raw } else { "" }
# nothing sensitive may reach the console: URLs and long tokens are redacted defensively
$text = [regex]::Replace($text, '(?i)[a-z][a-z0-9+.\-]*://[^@\s/]+@', '<redacted-url>@')
$text = [regex]::Replace($text, '[A-Za-z0-9+/=_\-]{40,}', '<redacted>')
Write-Host $text
if (-not $text) { Write-Host ("no output. The error stream is withheld (it can name hosts or users); the exit code was " + $p.ExitCode) -ForegroundColor Yellow }
Remove-Item $so, $se -ErrorAction SilentlyContinue

# the application code at HEAD: is there ANY broker / order-placing code path? (file names only)
Write-Host ""; Write-Host "=== 12. broker / order-placing code in the deployed tree (git grep at HEAD, file names only)"
$hits = @(& git --no-optional-locks -C $Root grep -l -i -E "place_order|submit_order|create_order|order_send|alpaca|ib_insync|mt5rest|metatrader|tradier|schwab|robinhood" HEAD -- "backend/*.py" 2>$null)
if ($hits.Count) { $hits | ForEach-Object { Write-Host ("  " + $_) } } else { Write-Host "  (none)" }
Write-Host ""; Write-Host "Read-only. Nothing was changed. Temporary files removed; the query program is kept for the record."
