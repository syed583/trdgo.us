# TRDGO-US: READ-ONLY trade-level evidence for the ChatGPT Intraday paper trades (and the other five strategies in summary).
# Every query runs in its own READ ONLY transaction that is rolled back, never committed. No connection string, key or token is read or printed.
# Writes ONE new file: chatgpt_trades_evidence_<stamp>.py in $ReportDir. Changes no database row, no git state, no service.
param([string]$Root = "C:\apps\us-stock-reader", [string]$ReportDir = "C:\audit-reports", [string]$AppEnv = "production",
      [string]$Strategy = "chatgpt", [int]$TimeoutSec = 120)
$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backend = Join-Path $Root "backend"; $py = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "STOPPED: no python at $py" -ForegroundColor Red; exit 2 }
$null = New-Item -ItemType Directory -Force -Path $ReportDir
$prog = Join-Path $ReportDir ("chatgpt_trades_evidence_" + $stamp + ".py")
@'
import os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, os.environ["TRDGO_BACKEND"])
ET = ZoneInfo("America/New_York")
from sqlalchemy import text
from database import engine
STRAT = os.environ.get("TRDGO_STRATEGY", "chatgpt")
def e(v, fmt="%m-%d %H:%M:%S"):
    return "-" if v is None else v.astimezone(ET).strftime(fmt)
def f(v, n=2):
    return "-" if v is None else f"{v:.{n}f}"
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
now = datetime.now(ET); today = now.date().isoformat()
print("ET now:", now.strftime("%a %Y-%m-%d %H:%M"), "| strategy:", STRAT)
with engine.connect() as c:
    sect("1. every strategy, today (session_date): opened / still OPEN / closed by status")
    for r in q(c, "SELECT strategy, count(*) AS n, count(*) FILTER (WHERE status='OPEN') AS open_n, count(*) FILTER (WHERE side='LONG') AS longs, count(*) FILTER (WHERE side='SHORT') AS shorts, count(*) FILTER (WHERE status='STOP') AS stop, count(*) FILTER (WHERE status='TARGET') AS target, count(*) FILTER (WHERE status='SCORE_EXIT') AS score_exit, count(*) FILTER (WHERE status='TIMED_EXIT') AS timed, min(entry_time) AS first_in, max(entry_time) AS last_in FROM demo_trades WHERE session_date = CAST(:t AS date) GROUP BY strategy ORDER BY strategy", t=today):
        print(f"  {r['strategy']:<11} opened {r['n']:<4} open {r['open_n']:<4} long {r['longs']:<4} short {r['shorts']:<4} stop {r['stop']:<3} target {r['target']:<3} score {r['score_exit']:<3} timed {r['timed']:<3} entries {e(r['first_in'])} .. {e(r['last_in'])} ET")
    sect("1b. every strategy, ALL HISTORY: totals, long/short, open/closed, exits by status, realized and unrealized P&L ($, 100 shares)")
    for r in q(c, "SELECT strategy, count(*) AS n, count(*) FILTER (WHERE status='OPEN') AS open_n, count(*) FILTER (WHERE status<>'OPEN') AS closed_n, count(*) FILTER (WHERE side='LONG') AS longs, count(*) FILTER (WHERE side='SHORT') AS shorts, count(*) FILTER (WHERE status='STOP') AS stop, count(*) FILTER (WHERE status='TARGET') AS target, count(*) FILTER (WHERE status='SCORE_EXIT') AS score_exit, count(*) FILTER (WHERE status='TIMED_EXIT') AS timed, round(coalesce(sum(profit) FILTER (WHERE status<>'OPEN'),0)::numeric,2) AS realized, round(coalesce(sum(profit) FILTER (WHERE status='OPEN'),0)::numeric,2) AS unrealized, min(session_date) AS first_day, max(session_date) AS last_day, round(min(score_at_entry)::numeric,1) AS s_lo, round(max(score_at_entry)::numeric,1) AS s_hi FROM demo_trades GROUP BY strategy ORDER BY strategy"):
        print(f"  {r['strategy']:<11} total {r['n']:<5} open {r['open_n']:<4} closed {r['closed_n']:<5} long {r['longs']:<5} short {r['shorts']:<5} | stop {r['stop']:<4} target {r['target']:<4} score {r['score_exit']:<4} timed {r['timed']:<4} | realized {r['realized']} unrealized {r['unrealized']} | days {r['first_day']}..{r['last_day']} | score_in {r['s_lo']}..{r['s_hi']}")
    sect("2. entry times, to the second (all strategies today): how many positions share each second")
    for r in q(c, "SELECT date_trunc('second', entry_time) AS s, count(*) AS n FROM demo_trades WHERE session_date = CAST(:t AS date) GROUP BY 1 ORDER BY n DESC, 1 LIMIT 8", t=today):
        print(f"  {e(r['s'])} ET  {r['n']} positions")
    sect("3. " + STRAT + " trades today: one line per trade")
    print("  id    symbol  side   shares  entry_et        entry     stop      target    stop%   tgt%   score_in  status      last      exit_et         exit_px   pnl")
    for r in q(c, "SELECT id, symbol, side, shares, entry_time, entry_price, stop, target, score_at_entry, status, last_price, exit_time, exit_price, profit FROM demo_trades WHERE session_date = CAST(:t AS date) AND strategy = :s ORDER BY entry_time, symbol", t=today, s=STRAT):
        ep = r["entry_price"]; sp = None if r["stop"] is None or not ep else (r["stop"] - ep) / ep * 100; tp = None if r["target"] is None or not ep else (r["target"] - ep) / ep * 100
        print(f"  {r['id']:<5} {r['symbol']:<7} {r['side']:<6} {r['shares']:<7} {e(r['entry_time']):<15} {f(ep):<9} {f(r['stop']):<9} {f(r['target']):<9} {f(sp,2):<7} {f(tp,2):<6} {f(r['score_at_entry'],1):<9} {r['status']:<11} {f(r['last_price']):<9} {e(r['exit_time']):<15} {f(r['exit_price']):<9} {f(r['profit'])}")
    sect("4. " + STRAT + ": score_at_entry distribution (how far past the 55 / 45 cutoffs the entries were)")
    for r in q(c, "SELECT side, count(*) AS n, min(score_at_entry) AS lo, round(avg(score_at_entry)::numeric,1) AS avg, max(score_at_entry) AS hi FROM demo_trades WHERE session_date = CAST(:t AS date) AND strategy = :s GROUP BY side", t=today, s=STRAT):
        print(f"  {r['side']:<6} n={r['n']:<4} score_at_entry min {r['lo']} avg {r['avg']} max {r['hi']}")
    sect("5. rows with missing levels or scores (these used the 1% / 2% default levels or have no score-drop exit)")
    for r in q(c, "SELECT count(*) FILTER (WHERE stop IS NULL OR target IS NULL) AS no_levels, count(*) FILTER (WHERE score_at_entry IS NULL) AS no_score, count(*) AS n FROM demo_trades WHERE session_date = CAST(:t AS date) AND strategy = :s", t=today, s=STRAT):
        print("  " + ", ".join(f"{k}={v}" for k, v in r.items()))
    sect("6. columns stored per trade (parameter readings are NOT among them unless listed)")
    print("  " + ", ".join(x["column_name"] for x in q(c, "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='demo_trades' ORDER BY ordinal_position")))
try: engine.dispose()
except Exception: pass
'@ | Set-Content -Path $prog -Encoding UTF8
Write-Host ("query program written (new file): " + $prog)
$so = Join-Path $ReportDir ("chatgpt_trades_evidence_" + $stamp + ".tmp"); $se = Join-Path $ReportDir ("chatgpt_trades_evidence_err_" + $stamp + ".tmp")
$env:APP_ENV = $AppEnv; $env:TRDGO_BACKEND = $backend; $env:PYTHONDONTWRITEBYTECODE = "1"; $env:TRDGO_STRATEGY = $Strategy
try {
    $p = Start-Process -FilePath $py -ArgumentList ('-I -X utf8 "' + $prog + '"') -WorkingDirectory $backend -NoNewWindow -PassThru -RedirectStandardOutput $so -RedirectStandardError $se
    if (-not $p.WaitForExit($TimeoutSec * 1000)) { try { $p.Kill() } catch { }; Write-Host ("timed out after " + $TimeoutSec + " s and was stopped") -ForegroundColor Red }
} finally { Remove-Item Env:APP_ENV, Env:TRDGO_BACKEND, Env:PYTHONDONTWRITEBYTECODE, Env:TRDGO_STRATEGY -ErrorAction SilentlyContinue }
$text = if (Test-Path $so) { Get-Content $so -Raw } else { "" }
$text = [regex]::Replace($text, '(?i)[a-z][a-z0-9+.\-]*://[^@\s/]+@', '<redacted-url>@')
$text = [regex]::Replace($text, '[A-Za-z0-9+/=_\-]{40,}', '<redacted>')
Write-Host $text
if (-not $text) { Write-Host "no output (the error stream is withheld): treat as NOT VERIFIED" -ForegroundColor Yellow }
Remove-Item $so, $se -ErrorAction SilentlyContinue
Write-Host ""; Write-Host "Read-only. Nothing was changed. Temporary files removed; the query program is kept for the record."
