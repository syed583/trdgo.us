# TRDGO-US read-only pre-deployment preflight for the PRODUCTION VPS. Paste this whole file into PowerShell on the VPS.
# Set the -ReleaseCommit default below (the "inner" script's param block) to the approved release SHA first; until then the
# "Release on remote" check fails closed. It writes only its own report files under C:udit-reports and changes nothing else
# (see docs/production_cutover_plan.md). The signal validator it embeds is backend/signal_recovery.py, unchanged.
$ReportDir = "C:\audit-reports"; $null = New-Item -ItemType Directory -Force -Path $ReportDir
@'
import json, os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
if os.environ.get("TRDGO_BACKEND"):
    sys.path.insert(0, os.environ["TRDGO_BACKEND"])
os.environ.setdefault("APP_ENV", "production")
out = {"queries": {}, "ro_violations": []}
# ---- signal_recovery.py, embedded unchanged (the server may still run the old build, which lacks it) ----
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REQUIRED_COLUMNS = ("id", "symbol", "session_date", "state", "side", "is_open", "entry", "tp", "sl", "best_price", "atr",
                    "be_moved", "score_in", "signal_ts", "status_ts")
_BUY_STATES, _SELL_STATES = ("BUY", "STRONG BUY"), ("SELL", "STRONG SELL")
_INDEX_ROOTS = frozenset({"SPX", "SPXW", "NDX", "NDXP", "RUT", "RUTW", "INDU", "DJX", "VIX", "VIXW", "COMP", "OEX", "XSP"})
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_FUTURE_SKEW = timedelta(minutes=5)


def instrument_kind(symbol: Any) -> str:
    """'equity' | 'index' | 'future' | 'unknown' -- the same rule as market_calendar.instrument_kind (a test pins them together)."""
    s = (str(symbol) if symbol is not None else "").strip().upper()
    if not s:
        return "unknown"
    if s.startswith("/") or s.endswith("=F"):
        return "future"
    if s.startswith("^") or s in _INDEX_ROOTS:
        return "index"
    return "equity" if s.replace(".", "").replace("-", "").isalnum() else "unknown"


def _ts(v: Any) -> Optional[datetime]:
    """A timestamp from a database driver (datetime) or a text column (ISO). Naive values are taken as UTC."""
    if v is None:
        return None
    try:
        if isinstance(v, datetime):
            d = v
        elif isinstance(v, str):
            d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        else:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _day(v: Any) -> Optional[date]:
    try:
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            return date.fromisoformat(v.strip()[:10])
    except (ValueError, TypeError):
        pass
    return None


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return f if math.isfinite(f) else None


def validate(rows: Iterable[dict], *, columns: Optional[Iterable[str]] = None, now: Optional[datetime] = None,
             cutoff: Any = None) -> dict:
    """Judge the OPEN signal rows. ``cutoff``: sessions older than this ISO date are stale. Returns
    {"ok", "fresh", "stale", "problems": [...], "fresh_rows": [{id, symbol, session, side}]}. Never raises."""
    problems: list[str] = []
    fresh_rows: list[dict] = []
    stale = 0
    try:
        now_et = (now or datetime.now(timezone.utc)).astimezone(ET)
        cut = _day(cutoff)
        rows = list(rows)
        if cut is None:
            problems.append("the carry-window cutoff is unknown")
        by_symbol: dict[str, list[dict]] = {}
        for r in rows:
            rid = r.get("id")
            tag = f"signal #{rid}"
            sd = _day(r.get("session_date"))
            if cut is not None and sd is not None and sd < cut:
                stale += 1                                     # outside the carry window: reported, never validated
                continue
            tag += f" {r.get('symbol')}"
            fresh = {"id": rid, "symbol": r.get("symbol"), "session": str(sd) if sd else None, "side": r.get("side")}
            fresh_rows.append(fresh)
            bad: list[str] = []
            if rid is None:
                bad.append("no id")
            if r.get("is_open") not in (True, 1):
                bad.append("is_open is not true")
            sym = r.get("symbol")
            if not isinstance(sym, str) or not _SYMBOL.match(sym) or instrument_kind(sym) != "equity":
                bad.append(f"symbol {sym!r} is not a US stock / ETF the engine can price")
            side, state = r.get("side"), r.get("state")
            if side not in ("BUY", "SELL"):
                bad.append(f"side {side!r} is neither BUY nor SELL")
            elif state not in (_BUY_STATES if side == "BUY" else _SELL_STATES):
                bad.append(f"state {state!r} does not agree with side {side}")
            if sd is None:
                bad.append("session_date is not a date")
            elif sd > now_et.date():
                bad.append(f"session_date {sd} is in the future")
            st, sts = _ts(r.get("signal_ts")), _ts(r.get("status_ts"))
            if st is None:
                bad.append("signal_ts is missing or unreadable")
            else:
                if st > now_et + _FUTURE_SKEW:
                    bad.append("signal_ts is in the future")
                if sd is not None and st.astimezone(ET).date() != sd:
                    bad.append(f"signal_ts falls on {st.astimezone(ET).date()}, not on session {sd}")
            if sts is None:
                bad.append("status_ts is missing or unreadable")
            elif st is not None and sts < st - timedelta(seconds=1):
                bad.append("status_ts is before signal_ts")
            entry, best, tp, sl, atr = (_num(r.get(k)) for k in ("entry", "best_price", "tp", "sl", "atr"))
            if entry is None or entry <= 0:
                bad.append("entry is not a positive number")
            if best is None or best <= 0:
                bad.append("best_price is not a positive number")
            if atr is not None and atr < 0:
                bad.append("atr is negative")
            if (tp is None) != (sl is None):
                bad.append("only one of tp / sl is set")
            elif tp is None:
                if atr:                                            # the engine only leaves levels empty when there is no ATR
                    bad.append("tp / sl are empty although an ATR is recorded")
            elif entry and entry > 0:
                if tp <= 0 or sl <= 0:
                    bad.append("tp / sl are not positive")
                elif side == "BUY" and not (sl < entry < tp):
                    bad.append(f"BUY levels are inconsistent (sl {sl} / entry {entry} / tp {tp})")
                elif side == "SELL" and not (tp < entry < sl):
                    bad.append(f"SELL levels are inconsistent (tp {tp} / entry {entry} / sl {sl})")
            be = r.get("be_moved")
            if be is not None and be not in (True, False, 0, 1):
                bad.append("be_moved is not true/false")
            for b in bad:
                problems.append(f"{tag}: {b}")
            if isinstance(sym, str):
                by_symbol.setdefault(sym, []).append(r)
        if fresh_rows and columns is not None:                  # a table without these columns matters only if something is open
            missing = [c for c in REQUIRED_COLUMNS if c not in set(columns)]
            if missing:
                problems.append("the signal table lacks columns the engine reads: " + ", ".join(missing))
        for sym, group in sorted(by_symbol.items()):
            if len(group) > 1:
                sides = sorted({str(g.get("side")) for g in group})
                kind = "opposite sides" if len(sides) > 1 else "the same side"
                problems.append(f"{sym} has {len(group)} open signals ({kind}): ids " +
                                ", ".join(str(g.get("id")) for g in group))
    except Exception as exc:  # noqa: BLE001 -- anything unexpected is a refusal
        problems.append(f"the open signals could not be validated ({type(exc).__name__})")
    return {"ok": not problems, "fresh": len(fresh_rows), "stale": stale, "problems": problems, "fresh_rows": fresh_rows}


def cutoff_iso(now: Optional[datetime] = None, trading_days: int = 1, is_trading_day=None) -> str:
    """The ISO date before which an open signal is stale: ``trading_days`` trading days back from today (Eastern).
    Weekends are skipped; ``is_trading_day`` (optional) adds exchange holidays."""
    d = (now or datetime.now(timezone.utc)).astimezone(ET).date()
    n = 0
    while n < trading_days:
        d -= timedelta(days=1)
        if d.weekday() < 5 and (is_trading_day is None or is_trading_day(d)):
            n += 1
    return d.isoformat()


def assess(engine=None, *, now: Optional[datetime] = None) -> dict:
    """Read the open signals from the database (read-only) and validate them. Fails closed on any read problem."""
    out = {"ok": False, "table": False, "fresh": 0, "stale": 0, "problems": [], "fresh_rows": []}
    try:
        from sqlalchemy import inspect, text
        import entry_lock
        eng = entry_lock._engine(engine)
        insp = inspect(eng)
        if "intraday_sig_v2" not in insp.get_table_names():
            return {**out, "ok": True, "problems": []}                        # no table = nothing open
        out["table"] = True
        cols = [c["name"] for c in insp.get_columns("intraday_sig_v2")]
        missing = [c for c in ("is_open", "session_date") if c not in cols]
        if missing:
            return {**out, "problems": ["the signal table lacks " + ", ".join(missing) + ": cannot tell what is open"]}
        cut = entry_lock._cutoff(now)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
            pick = [c for c in REQUIRED_COLUMNS if c in cols]
            rows = conn.execute(text("SELECT " + ", ".join(pick) + " FROM intraday_sig_v2 WHERE is_open = :t ORDER BY id"),
                                {"t": True}).mappings().all()
        res = validate([dict(r) for r in rows], columns=cols, now=now, cutoff=cut)
        return {**res, "table": True}
    except Exception as exc:  # noqa: BLE001
        return {**out, "problems": [f"could not read the open signals ({type(exc).__name__}): refusing to proceed blind"]}
# ---- end of signal_recovery.py ----
try:
    from sqlalchemy import text
    from database import engine
except Exception as e:
    print(json.dumps({"fatal": "import failed: " + type(e).__name__})); sys.exit(0)
def prev_weekday(d, n):
    k = 0
    while k < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            k += 1
    return d
et = datetime.now(ZoneInfo("America/New_York"))
today = et.date()
cut = prev_weekday(today, 1).isoformat()      # one trading day back (exchange holidays are not modelled here)
out["et_now"] = et.isoformat(timespec="seconds"); out["today"] = today.isoformat(); out["cutoff"] = cut
P = {"t": today.isoformat(), "c": cut}
def q(c, name, sql):
    """ONE read-only statement per transaction: SET TRANSACTION READ ONLY, proven with SHOW, then a ROLLBACK (never a
    commit). A failure is recorded as a failure: no value is ever inferred."""
    res = {"ok": False}
    try:
        s = sql.strip()
        if not s.upper().startswith(("SELECT", "SHOW")) or ";" in s:
            raise ValueError("refused: not a single read-only statement")
        c.execute(text("SET TRANSACTION READ ONLY"))
        if str(c.execute(text("SHOW transaction_read_only")).scalar()).lower() != "on":
            out["ro_violations"].append(name)
            raise RuntimeError("the transaction is not read-only")
        res = {"ok": True, "rows": [dict(x) for x in c.execute(text(s), P).mappings().all()]}
    except Exception as e:
        res = {"ok": False, "error": type(e).__name__}
    finally:
        try: c.rollback()
        except Exception: pass
    out["queries"][name] = res
try:
    with engine.connect() as c:
        q(c, "version", "SELECT version() AS v")
        q(c, "db", "SELECT round(pg_database_size(current_database())/1048576.0) AS size_mb, (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()) AS connections")
        q(c, "extensions", "SELECT extname FROM pg_extension ORDER BY 1")
        q(c, "public_tables", "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
        for t in ("trade_calls", "model_predictions", "earnings_signal_snapshots", "ped_scores"):
            q(c, "n_" + t, "SELECT count(*) AS n FROM " + t)
        q(c, "demo_open", "SELECT count(*) AS n FROM demo_trades WHERE status='OPEN'")
        q(c, "demo_open_by_strategy", "SELECT strategy, count(*) AS n FROM demo_trades WHERE status='OPEN' GROUP BY strategy ORDER BY strategy")
        q(c, "demo_carried", "SELECT count(*) AS n FROM demo_trades WHERE status='OPEN' AND session_date < CAST(:t AS date)")
        q(c, "demo_stuck_30h", "SELECT count(*) AS n FROM demo_trades WHERE status='OPEN' AND entry_time < now() - interval '30 hours'")
        q(c, "demo_activity", "SELECT max(entry_time) AS last_entry, max(exit_time) AS last_exit, count(*) FILTER (WHERE entry_time > now() - interval '60 minutes') AS entries_last_60min FROM demo_trades")
        q(c, "sig_today", "SELECT count(*) AS n FROM intraday_sig_v2 WHERE is_open AND session_date = CAST(:t AS date)")
        q(c, "sig_carried_fresh", "SELECT count(*) AS n FROM intraday_sig_v2 WHERE is_open AND session_date >= CAST(:c AS date) AND session_date < CAST(:t AS date)")
        q(c, "sig_stale", "SELECT id, symbol, session_date, side FROM intraday_sig_v2 WHERE is_open AND session_date < CAST(:c AS date) ORDER BY session_date, id")
        q(c, "sig_duplicates", "SELECT symbol, count(*) AS n, array_agg(id ORDER BY id) AS ids FROM intraday_sig_v2 WHERE is_open GROUP BY symbol HAVING count(*) > 1 ORDER BY symbol")
        q(c, "sig_activity", "SELECT max(signal_ts) AS last_signal_ts FROM intraday_sig_v2")
        q(c, "sig_open_rows", "SELECT id, symbol, session_date, state, side, is_open, entry, tp, sl, best_price, atr, be_moved, score_in, signal_ts, status_ts FROM intraday_sig_v2 WHERE is_open AND session_date >= CAST(:c AS date) ORDER BY id")
        _rows = out["queries"].get("sig_open_rows", {})
        if _rows.get("ok"):
            out["sig_validation"] = validate(_rows["rows"], columns=list(REQUIRED_COLUMNS), now=datetime.now(timezone.utc), cutoff=cut)
        q(c, "lock_table", "SELECT to_regclass('trade_entry_lock') IS NOT NULL AS present")
        lt = out["queries"].get("lock_table", {})
        if lt.get("ok") and lt["rows"] and lt["rows"][0].get("present"):
            q(c, "lock_row", "SELECT locked, reason, updated_at FROM trade_entry_lock WHERE id = 1")
        q(c, "lock_triggers", "SELECT c.relname AS tbl, t.tgname AS trigger_name FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE NOT t.tgisinternal AND c.relname IN ('demo_trades','intraday_sig_v2') ORDER BY 1,2")
except Exception as e:
    out["fatal"] = "connection failed: " + type(e).__name__
finally:
    try: engine.dispose()
    except Exception: pass
out["queries"].get("sig_open_rows", {}).pop("rows", None) if out["queries"].get("sig_open_rows", {}).get("ok") else None
out.setdefault("sig_rows_n", len(out.get("sig_validation", {}).get("fresh_rows", [])))
print(json.dumps(out, default=str))
'@ | Set-Content (Join-Path $ReportDir "preflight_ro.py") -Encoding UTF8

@'
param([string]$Root = "C:\apps\us-stock-reader", [string]$ServiceName = "USStockReader", [int]$Port = 8000,
      [string]$DumpDir = "C:\audit-reports\db-backups",
      [string]$TestedDump = "trdgo_us_20261026_predeploy.dump",
      [string]$RestoreLog = "C:\audit-reports\pg17-public-restore.log",
      [string]$BaseCommit = "36094e89ff78c531bd6c8997208d31f02f1aafee",
      [string]$ReleaseCommit = "REPLACE_WITH_THE_APPROVED_RELEASE_SHA",
      [string]$Branch = "release/trdgo-us-safety-20261026",
      [string]$PythonExe = "",
      [string]$PgRestore = "",
      [string]$ReportDir = "C:\audit-reports",
      [int]$MaxDumpAgeMin = 60,
      [int]$DbTimeoutSec = 180)
$ErrorActionPreference = "SilentlyContinue"; $Error.Clear()
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"; $rd = $ReportDir; $null = New-Item -ItemType Directory -Force -Path $rd
$R = New-Object System.Collections.Generic.List[object]

# ---- nothing sensitive may reach the console or the report: connection URLs, secrets, long tokens ---------------
function Redact([string]$t) {
    if ($null -eq $t) { return "" }
    $t = [regex]::Replace($t, '(?i)[a-z][a-z0-9+.\-]*://[^@\s/]+@', '<redacted-url>@')
    $t = [regex]::Replace($t, '(?i)((?:password|passwd|pwd|secret|token|api[_-]?key|authorization|PGPASSWORD|PGUSER|DATABASE_URL|UNUSUAL_WHALES\w*)\s*[=:]\s*)\S+', '$1<redacted>')
    $t = [regex]::Replace($t, '(?i)(bearer\s+)\S{12,}', '$1<redacted>')
    $t = [regex]::Replace($t, '[A-Za-z0-9+/=_\-]{40,}', { param($m) if ($m.Value -match '^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$') { $m.Value } else { '<redacted>' } })
    return $t
}
function Add-R([string]$status, [string]$name, [string]$detail, [bool]$mandatory = $false) {
    $R.Add([pscustomobject]@{ Status = $status; Name = $name; Detail = (Redact $detail); Mandatory = $mandatory })
}
function Http-Get([string]$url, [int]$timeout = 8) {
    try { $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeout; return @{ ok = $true; code = [int]$resp.StatusCode; body = [string]$resp.Content } }
    catch { $c = $null; if ($_.Exception.Response) { $c = [int]$_.Exception.Response.StatusCode }; return @{ ok = $false; code = $c; body = "" + $_.Exception.Message } }
}
$backend = Join-Path $Root "backend"
$py = $PythonExe; if (-not $py) { $py = Join-Path $backend ".venv\Scripts\python.exe" }

# ---- git ---------------------------------------------------------------------------------------------------------
$head = ("" + (git -C $Root rev-parse HEAD 2>$null)).Trim()
if ($head -and $head -eq $BaseCommit) { Add-R "PASS" "Server commit" ("HEAD = " + $head.Substring(0, 12) + " (the audited base)") $true }
elseif ($head) { Add-R "FAIL" "Server commit" ("HEAD = " + $head + " is not the audited base " + $BaseCommit.Substring(0, 12) + "; the plan must be re-checked") $true }
else { Add-R "NOT VERIFIED" "Server commit" "git could not read HEAD" $true }
Add-R "INFO" "Server branch" ((git -C $Root rev-parse --abbrev-ref HEAD 2>$null) + " (upstream " + (git -C $Root rev-parse --abbrev-ref "@{u}" 2>$null) + ")")
$remote = ("" + (git -C $Root ls-remote origin ("refs/heads/" + $Branch) 2>$null)).Trim()
if ($remote -and $remote.StartsWith($ReleaseCommit)) { Add-R "PASS" "Release on remote" ($Branch + " = " + $ReleaseCommit.Substring(0, 12)) $true }
elseif ($remote) { Add-R "FAIL" "Release on remote" ("remote branch is " + ($remote -split "\s")[0] + ", not the approved " + $ReleaseCommit.Substring(0, 12)) $true }
else { Add-R "NOT VERIFIED" "Release on remote" "ls-remote returned nothing (network or credentials)" $true }
$dirty = @(git --no-optional-locks -C $Root status --porcelain 2>$null); $statusOk = ($LASTEXITCODE -eq 0)
$other = @($dirty | Where-Object { $_ -notmatch '^( M|\?\?) (Data_Health_Check\.xlsx|backend/diag[\w]*\.py)$' })
if (-not $head -or -not $statusOk) { Add-R "NOT VERIFIED" "Working tree" "git could not read the repository status" $true }
elseif ($other.Count -eq 0) { Add-R "PASS" "Working tree" ("only the known local files (" + $dirty.Count + "): Data_Health_Check.xlsx and backend/diag*.py; no application code edited") $true }
else { Add-R "FAIL" "Working tree" ("unexpected local changes: " + (($other | Select-Object -First 5) -join "; ")) $true }

# ---- service + API ---------------------------------------------------------------------------------------------
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$h = Http-Get ("http://127.0.0.1:" + $Port + "/health")
if ($svc -and $svc.Status -eq "Running" -and $h.ok -and $h.body -match '"status"\s*:\s*"ok"') { Add-R "PASS" "Service and /health" ($ServiceName + " Running; " + $h.body) $true }
else { Add-R "FAIL" "Service and /health" ("service " + $(if ($svc) { $svc.Status } else { "not found" }) + "; health " + $h.code + " " + $h.body) $true }
$s = Http-Get ("http://127.0.0.1:" + $Port + "/safety")
if ($s.ok -and $s.body.TrimStart().StartsWith("<")) { Add-R "INFO" "/safety" "HTML = the running build has no safety route (the old build; expected before the cutover)" }
elseif ($s.ok) { Add-R "INFO" "/safety" $s.body }
else { Add-R "INFO" "/safety" ("not reachable: " + $s.code) }

# ---- backup evidence (no restore is run). Freshness counts ONLY a dump that was validated. ---------------------------
function Find-PgRestore {
    if ($PgRestore -and (Test-Path $PgRestore)) { return $PgRestore }
    # no recursive disk search (it can be slow): PATH, then the usual fixed locations, then one level of C:\pg* folders
    $c = @((Get-Command pg_restore -ErrorAction SilentlyContinue).Source)
    $c += @("C:\pgtools17\pgsql\bin\pg_restore.exe", "C:\pgsql\bin\pg_restore.exe", "C:\Program Files\PostgreSQL\17\bin\pg_restore.exe")
    foreach ($dir in @(Get-ChildItem "C:\" -Directory -Filter "pg*" -ErrorAction SilentlyContinue)) { $c += (Join-Path $dir.FullName "pgsql\bin\pg_restore.exe"); $c += (Join-Path $dir.FullName "bin\pg_restore.exe") }
    return ($c | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1)
}
function Test-Dump($f, [bool]$restoreLogProvesIt) {
    $res = [pscustomobject]@{ Ok = $false; Reason = ""; Sha = "" }
    if ($f.Length -lt 1MB) { $res.Reason = "smaller than 1 MB (" + $f.Length + " bytes)"; return $res }
    $len = $f.Length; Start-Sleep -Seconds 2; $f.Refresh()
    if ($f.Length -ne $len) { $res.Reason = "the file is still being written (its size changed)"; return $res }
    if ($restoreLogProvesIt) { $res.Reason = "restore-tested: the restore log is clean" }
    else {
        $tool = Find-PgRestore
        if (-not $tool) { $res.Reason = "no pg_restore found, so its contents cannot be read"; return $res }
        $verText = "" + ((& $tool --version) -join " ")
        $major = 0; if ($verText -match '(\d+)(?:\.\d+)*\s*$') { $major = [int]$matches[1] }
        if ($major -lt 17) { $res.Reason = "pg_restore is version " + $major + " (17 or newer is needed)"; return $res }
        $toc = @(& $tool --list $f.FullName 2>$null); $code = $LASTEXITCODE
        $entries = @($toc | Where-Object { $_ -and $_ -notmatch '^;' })
        if ($code -ne 0 -or $entries.Count -lt 10) { $res.Reason = "pg_restore --list failed or read too few entries (exit " + $code + ", " + $entries.Count + ")"; return $res }
        $res.Reason = "pg_restore --list read " + $entries.Count + " entries"
    }
    $res.Sha = (Get-FileHash $f.FullName -Algorithm SHA256).Hash
    $res.Ok = $true
    return $res
}
$dumps = @(); if (Test-Path $DumpDir) { $dumps = @(Get-ChildItem $DumpDir -Filter *.dump -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending) }
$tested = $null; if (Test-Path (Join-Path $DumpDir $TestedDump)) { $tested = Get-Item (Join-Path $DumpDir $TestedDump) }
if ($tested) { Add-R "PASS" "Restore-tested dump present" ($tested.Name + ", " + [math]::Round($tested.Length / 1MB, 1) + " MB, written " + $tested.LastWriteTime) $true }
else { Add-R "FAIL" "Restore-tested dump present" ("not found: " + (Join-Path $DumpDir $TestedDump)) $true }
$restoreLogOk = $false
if (Test-Path $RestoreLog) {
    $log = @(Get-Content $RestoreLog -ErrorAction SilentlyContinue)
    $errs = @($log | Where-Object { $_ -match '(?i)\berror\b|\bfatal\b|could not|failed' }).Count
    $logTime = (Get-Item $RestoreLog).LastWriteTime
    if ($log.Count -eq 0) { Add-R "NOT VERIFIED" "Restore log" ($RestoreLog + " is empty: no evidence") $true }
    elseif ($errs -gt 0) { Add-R "FAIL" "Restore log" ("" + $errs + " error/fatal/failed lines in " + $RestoreLog) $true }
    elseif ($tested -and $logTime -lt $tested.LastWriteTime) { Add-R "NOT VERIFIED" "Restore log" "the log is older than the tested dump: it is not evidence for that dump" $true }
    else { $restoreLogOk = $true; Add-R "PASS" "Restore log" ($RestoreLog + ": " + $log.Count + " lines, 0 error lines, written " + $logTime) $true }
} else { Add-R "NOT VERIFIED" "Restore log" ("not found: " + $RestoreLog) $true }
$validated = $null; $vres = $null; $rejected = @()
foreach ($f in @($dumps | Select-Object -First 8)) {
    $isTested = [bool]($tested -and $f.FullName -eq $tested.FullName)
    $v = Test-Dump $f ($isTested -and $restoreLogOk)
    if ($v.Ok) { $validated = $f; $vres = $v; break } else { $rejected += ($f.Name + ": " + $v.Reason) }
}
if ($rejected.Count -gt 0) { Add-R "INFO" "Dumps rejected (never counted for freshness)" ($rejected -join " | ") }
if ($validated) { Add-R "PASS" "Validated dump" ($validated.Name + ", " + [math]::Round($validated.Length / 1MB, 1) + " MB, written " + $validated.LastWriteTime + ", SHA-256 " + $vres.Sha + "; " + $vres.Reason) $true }
else { Add-R "FAIL" "Validated dump" "no dump could be validated (size, stable, readable by pg_restore 17 or restore-tested)" $true }
$dumpAgeMin = $null; if ($validated) { $dumpAgeMin = [math]::Round(((Get-Date) - $validated.LastWriteTime).TotalMinutes) }
if ($null -eq $dumpAgeMin) { Add-R "FAIL" "Fresh backup" "no validated dump to age" $true }
elseif ($dumpAgeMin -le $MaxDumpAgeMin) { Add-R "PASS" "Fresh backup" ("the newest VALIDATED dump is " + $dumpAgeMin + " min old (limit " + $MaxDumpAgeMin + ")") $true }
else { Add-R "FAIL" "Fresh backup" ("the newest validated dump is " + [math]::Round($dumpAgeMin / 60, 1) + " h old: a NEW pre-deployment dump is REQUIRED within " + $MaxDumpAgeMin + " min of the cutover (the old engine keeps writing; a restore would lose everything written since the dump)") $true }
if (Get-NetTCPConnection -State Listen -LocalPort 5433 -ErrorAction SilentlyContinue) { Add-R "INFO" "Scratch restore instance" "port 5433 is listening: stop it after the checks (pg_ctl stop); it is not production" }

# ---- database: one read-only transaction per query, run in a child process with a time limit -----------------------
$d = $null; $dbNote = $null
if (-not (Test-Path $py)) { $dbNote = "no python interpreter at " + $py }
elseif (-not (Test-Path (Join-Path $backend "database.py"))) { $dbNote = "backend\database.py not found" }
else {
    $so = Join-Path $rd ("preflight_out_" + $stamp + ".tmp"); $se = Join-Path $rd ("preflight_err_" + $stamp + ".tmp")
    $env:APP_ENV = "production"; $env:TRDGO_BACKEND = $backend; $env:PYTHONDONTWRITEBYTECODE = "1"
    try {
        $p = Start-Process -FilePath $py -ArgumentList ('-I -X utf8 "' + (Join-Path $rd "preflight_ro.py") + '"') -WorkingDirectory $backend -NoNewWindow -PassThru -RedirectStandardOutput $so -RedirectStandardError $se
        if (-not $p.WaitForExit($DbTimeoutSec * 1000)) { try { $p.Kill() } catch { }; $dbNote = "the database check timed out after " + $DbTimeoutSec + " s and was stopped" }
    } catch { $dbNote = "could not start the database check: " + $_.Exception.GetType().Name }
    finally { Remove-Item Env:APP_ENV -ErrorAction SilentlyContinue; Remove-Item Env:TRDGO_BACKEND -ErrorAction SilentlyContinue; Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue }
    if (-not $dbNote) {
        $text = ""; if (Test-Path $so) { $text = (Get-Content $so -Raw -ErrorAction SilentlyContinue) }
        $line = (("" + $text) -split "`n" | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1)
        if (-not $line) { $dbNote = "the database check produced no JSON output" }
        else { try { $d = $line | ConvertFrom-Json -ErrorAction Stop } catch { $dbNote = "the database output was not valid JSON"; $d = $null } }
    }
    Remove-Item $so, $se -ErrorAction SilentlyContinue          # the child's stderr may name hosts or users: it is never kept or shown
}
function Q([string]$name) {
    if ($d -and $d.queries -and $d.queries.PSObject.Properties[$name]) { $x = $d.queries.$name; if ($x -and $x.ok -eq $true) { return $x } }
    return $null
}
function Cnt([string]$name) {
    $x = Q $name
    if ($x -and @($x.rows).Count -eq 1) { $v = @($x.rows)[0].n; if ($null -ne $v -and ("" + $v) -match '^\d+$') { return [int64]$v } }
    return $null
}
function Row1([string]$name) { $x = Q $name; if ($x -and @($x.rows).Count -ge 1) { return @($x.rows)[0] }; return $null }
function Show($v) { if ($null -eq $v) { return "NOT VERIFIED" } else { return "" + $v } }

if ($dbNote -or -not $d -or $d.fatal) {
    $why = $dbNote; if (-not $why -and $d) { $why = $d.fatal }; if (-not $why) { $why = "no data" }
    Add-R "NOT VERIFIED" "Database read-only session" ("could not read the database: " + $why) $true
    Add-R "NOT VERIFIED" "Book is flat" "the database could not be read: never assumed flat" $true
    Add-R "NOT VERIFIED" "Required database queries" "the database could not be read: no query was verified" $true
} else {
    $ver = Row1 "version"
    $viol = @($d.ro_violations)
    if ($viol.Count -gt 0) { Add-R "FAIL" "Database read-only session" ("a transaction was NOT read-only for: " + ($viol -join ", ") + "; stop and report") $true }
    elseif ($ver) { Add-R "PASS" "Database read-only session" ("every query ran in its own READ ONLY transaction, rolled back, never committed; " + $ver.v) $true }
    else { Add-R "NOT VERIFIED" "Database read-only session" "the version query failed, so read-only could not be proven" $true }
    $failed = @($d.queries.PSObject.Properties | Where-Object { $_.Value.ok -ne $true } | ForEach-Object { $_.Name })
    if ($failed.Count -gt 0) { Add-R "INFO" "Queries that failed (reported as NOT VERIFIED, never as zero)" ($failed -join ", ") }
    # ---- EVERY required query must have run, succeeded and returned well-formed data. A successful version query proves nothing else.
    function Num($v) { return ($null -ne $v -and ("" + $v) -match '^-?\d+(\.\d+)?$') }
    $expectedQ = @("version", "db", "extensions", "public_tables", "n_trade_calls", "n_model_predictions", "n_earnings_signal_snapshots", "n_ped_scores", "demo_open", "demo_open_by_strategy", "demo_carried", "demo_stuck_30h", "demo_activity", "sig_today", "sig_carried_fresh", "sig_stale", "sig_duplicates", "sig_activity", "sig_open_rows", "lock_table", "lock_triggers")
    $ltr = Row1 "lock_table"; $lockPresent = $null; if ($ltr -and ($ltr.present -is [bool])) { $lockPresent = $ltr.present }
    if ($lockPresent -eq $true) { $expectedQ += "lock_row" }
    $names = @($d.queries.PSObject.Properties | ForEach-Object { $_.Name })
    $unverified = @(); $invalid = @()
    foreach ($n in $expectedQ) { if ($names -notcontains $n) { $unverified += ($n + " (missing)") } elseif (-not (Q $n)) { $unverified += ($n + " (failed)") } }
    $x = Row1 "version"; if ($x -and -not (("" + $x.v) -match 'PostgreSQL')) { $invalid += "version (not a PostgreSQL version string)" }
    $x = Row1 "db"; if ((Q "db") -and -not ($x -and (Num $x.size_mb) -and (Num $x.connections))) { $invalid += "db (size/connections invalid)" }
    $x = Q "extensions"; if ($x -and (@($x.rows).Count -lt 1 -or @($x.rows | Where-Object { -not $_.extname }).Count -gt 0)) { $invalid += "extensions (invalid)" }
    foreach ($n in @("public_tables", "n_trade_calls", "n_model_predictions", "n_earnings_signal_snapshots", "n_ped_scores", "demo_open", "demo_carried", "demo_stuck_30h", "sig_today", "sig_carried_fresh")) { if ((Q $n) -and $null -eq (Cnt $n)) { $invalid += ($n + " (not a count)") } }
    $pt = Cnt "public_tables"; if ($null -ne $pt -and $pt -lt 1) { $invalid += "public_tables (zero tables)" }
    $by = Q "demo_open_by_strategy"
    if ($by) {
        $sum = 0; $okRows = $true
        foreach ($brow in @($by.rows)) { if (-not $brow.strategy -or -not (Num $brow.n)) { $okRows = $false } else { $sum += [int64]$brow.n } }
        if (-not $okRows) { $invalid += "demo_open_by_strategy (malformed rows)" }
        elseif ($null -ne (Cnt "demo_open") -and $sum -ne (Cnt "demo_open")) { $invalid += ("demo_open_by_strategy (sums to " + $sum + " but demo_open is " + (Cnt "demo_open") + ")") }
    }
    $x = Row1 "demo_activity"; if ((Q "demo_activity") -and -not ($x -and (Num $x.entries_last_60min))) { $invalid += "demo_activity (invalid)" }
    $x = Q "sig_stale"; if ($x -and @($x.rows | Where-Object { -not (Num $_.id) -or -not $_.symbol }).Count -gt 0) { $invalid += "sig_stale (malformed rows)" }
    $x = Q "sig_duplicates"; if ($x -and @($x.rows | Where-Object { -not $_.symbol -or -not (Num $_.n) -or [int64]$_.n -lt 2 }).Count -gt 0) { $invalid += "sig_duplicates (malformed rows)" }
    $x = Row1 "sig_activity"; if ((Q "sig_activity") -and -not ($x -and $x.PSObject.Properties["last_signal_ts"])) { $invalid += "sig_activity (invalid)" }
    if ((Q "lock_table") -and $null -eq $lockPresent) { $invalid += "lock_table (present is not true/false)" }
    if ($lockPresent -eq $true) { $x = Row1 "lock_row"; if ((Q "lock_row") -and -not ($x -and ($x.locked -is [bool]))) { $invalid += "lock_row (locked is not true/false)" } }
    $x = Q "lock_triggers"; if ($x -and @($x.rows | Where-Object { -not $_.tbl -or -not $_.trigger_name }).Count -gt 0) { $invalid += "lock_triggers (malformed rows)" }
    if ($unverified.Count -gt 0) { Add-R "NOT VERIFIED" "Required database queries" ("did not complete: " + ($unverified -join ", ") + $(if ($invalid.Count -gt 0) { "; also invalid: " + ($invalid -join ", ") } else { "" })) $true }
    elseif ($invalid.Count -gt 0) { Add-R "FAIL" "Required database queries" ("returned invalid data: " + ($invalid -join ", ")) $true }
    else { Add-R "PASS" "Required database queries" ("all " + $expectedQ.Count + " required queries ran, succeeded and returned well-formed data") $true }
    $db = Row1 "db"; if ($db) { Add-R "INFO" "Database size / connections" ("" + $db.size_mb + " MB, " + $db.connections + " connections") }
    Add-R "INFO" "Application tables" ("public tables " + (Show (Cnt "public_tables")) + "; trade_calls " + (Show (Cnt "n_trade_calls")) + ", model_predictions " + (Show (Cnt "n_model_predictions")) + ", earnings_signal_snapshots " + (Show (Cnt "n_earnings_signal_snapshots")) + ", ped_scores " + (Show (Cnt "n_ped_scores")) + "   (restore test: 55 / 129990 / 61270 / 15160 / 6406; production may be higher by what was written since the dump)")
    $openDemo = Cnt "demo_open"; $sigToday = Cnt "sig_today"; $sigCarried = Cnt "sig_carried_fresh"
    $by = Q "demo_open_by_strategy"; $bs = "NOT VERIFIED"; if ($by) { $bs = ((@($by.rows) | ForEach-Object { $_.strategy + "=" + $_.n }) -join ", ") }
    $act = Row1 "demo_activity"; $al = "NOT VERIFIED"; if ($act) { $al = "last entry " + $act.last_entry + "; entries in the last 60 min " + $act.entries_last_60min }
    Add-R "INFO" "Open paper positions" ((Show $openDemo) + " (" + $bs + "); carried from an earlier date " + (Show (Cnt "demo_carried")) + "; open over 30 h " + (Show (Cnt "demo_stuck_30h")) + "; " + $al)
    $stale = Q "sig_stale"; $act2 = Row1 "sig_activity"
    $staleN = "NOT VERIFIED"; if ($stale) { $staleN = "" + @($stale.rows).Count }
    Add-R "INFO" "Signal rows" ("open today " + (Show $sigToday) + ", carried inside the window " + (Show $sigCarried) + ", STALE " + $staleN + "; last signal " + $(if ($act2) { $act2.last_signal_ts } else { "NOT VERIFIED" }) + "; ET now " + $d.et_now + ", cutoff " + $d.cutoff)
    if ($stale -and @($stale.rows).Count -gt 0) { Add-R "INFO" "Stale open signals (ids only, nothing modified)" ((@($stale.rows) | ForEach-Object { "#" + $_.id + " " + $_.symbol + " " + $_.session_date + " " + $_.side }) -join "; ") }
    $dup = Q "sig_duplicates"; if ($dup -and @($dup.rows).Count -gt 0) { Add-R "INFO" "Symbols open more than once" ((@($dup.rows) | ForEach-Object { $_.symbol + " x" + $_.n + " ids " + (($_.ids) -join ",") }) -join "; ") }
    # ---- 1) open PAPER POSITIONS must be exactly zero (read successfully, never inferred)
    if ($viol.Count -gt 0) { Add-R "FAIL" "Paper positions are zero" "not accepted: a query ran outside a read-only transaction" $true }
    elseif ($null -eq $openDemo) { Add-R "NOT VERIFIED" "Paper positions are zero" "the open paper position count could not be read; never assumed zero" $true }
    elseif ($openDemo -eq 0) { Add-R "PASS" "Paper positions are zero" "the count was read and is exactly 0" $true }
    else { Add-R "FAIL" "Paper positions are zero" ("" + $openDemo + " open paper positions: the cutover preflight will refuse. Nothing is closed by this check.") $true }
    # ---- 2) open live SIGNALS may be carried through a forward cutover ONLY if every one passes the recovery checks
    $sv = $d.sig_validation
    $svRows = Q "sig_open_rows"
    $freshCount = $null; if ($null -ne $sigToday -and $null -ne $sigCarried) { $freshCount = [int64]($sigToday + $sigCarried) }
    if ($viol.Count -gt 0) { Add-R "FAIL" "Open signals recoverable" "not accepted: a query ran outside a read-only transaction" $true }
    elseif (-not $svRows -or -not $sv -or $sv.ok -isnot [bool] -or $null -eq $freshCount) { Add-R "NOT VERIFIED" "Open signals recoverable" "the open signals could not be read and validated; never assumed recoverable" $true }
    elseif ([int64]$sv.fresh -ne $freshCount) { Add-R "FAIL" "Open signals recoverable" ("the signal rows read (" + $sv.fresh + ") do not match the counts (" + $freshCount + "): the book changed between queries or the data is inconsistent") $true }
    elseif ($sv.ok -eq $true -and [int64]$sv.fresh -eq 0) { Add-R "PASS" "Open signals recoverable" ("no open signal inside the carry window; " + $sv.stale + " stale row(s) are reported only") $true }
    elseif ($sv.ok -eq $true) { Add-R "PASS" "Open signals recoverable" ("" + $sv.fresh + " open signal(s) pass every recovery check and will be managed by the new engine after the cutover: " + ((@($sv.fresh_rows) | ForEach-Object { "#" + $_.id + " " + $_.symbol + " " + $_.side }) -join "; ") + "; " + $sv.stale + " stale row(s) are reported only") $true }
    else { Add-R "FAIL" "Open signals recoverable" ("" + @($sv.problems).Count + " problem(s), the cutover preflight will refuse: " + ((@($sv.problems) | Select-Object -First 6) -join " | ")) $true }
    $lt = Row1 "lock_table"
    if ($lt -and $lt.present -eq $true) { $lr = Row1 "lock_row"; $trg = Q "lock_triggers"; Add-R "INFO" "Entry lock" ("table present; " + $(if ($lr) { "locked=" + $lr.locked } else { "state NOT VERIFIED" }) + "; triggers " + $(if ($trg) { (@($trg.rows) | ForEach-Object { $_.tbl + ":" + $_.trigger_name }) -join ", " } else { "NOT VERIFIED" })) }
    elseif ($lt) { Add-R "INFO" "Entry lock" "no trade_entry_lock table yet: this build has no lock (expected; the cutover installs it LOCKED)" }
    else { Add-R "INFO" "Entry lock" "NOT VERIFIED (query failed)" }
    $ex = Q "extensions"; if ($ex) { Add-R "INFO" "Database extensions" ((@($ex.rows) | ForEach-Object { $_.extname }) -join ", ") }
}

# ---- pooler / connection settings, Vault ---------------------------------------------------------------------------
$envLines = @(Get-Content (Join-Path $backend ".env") -ErrorAction SilentlyContinue)
$url = ($envLines | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1)
$m = [regex]::Match(("" + $url), '@(?<host>[^:/]+):(?<port>\d+)/')
$prep = $false; if (Test-Path (Join-Path $backend "database.py")) { $prep = [bool](Select-String -Path (Join-Path $backend "database.py") -Pattern "prepare_threshold" -Quiet) }
$appEnv = ("" + ($envLines | Where-Object { $_ -match '^APP_ENV=' } | Select-Object -First 1)) -replace '^APP_ENV=', ''
if ($m.Success -and $m.Groups["port"].Value -eq "6543" -and $prep) { Add-R "PASS" "Pooler settings" ("transaction pooler :6543 with prepared statements disabled (database.py); APP_ENV=" + $appEnv) $true }
elseif ($m.Success) { Add-R "FAIL" "Pooler settings" ("port " + $m.Groups["port"].Value + ", prepare_threshold handling " + $prep) $true }
else { Add-R "NOT VERIFIED" "Pooler settings" "DATABASE_URL not readable" $true }
$vault = @(Get-ChildItem (Join-Path $backend "*.py") -ErrorAction SilentlyContinue | Select-String -Pattern "vault|pgsodium|CREATE EXTENSION" -ErrorAction SilentlyContinue)
if (-not (Test-Path (Join-Path $backend "database.py"))) { Add-R "NOT VERIFIED" "Vault / extension dependency" "backend folder not readable" $true }
elseif ($vault.Count -eq 0) { Add-R "PASS" "No Supabase Vault / extension dependency" "no backend module references vault, pgsodium or CREATE EXTENSION" $true }
else { Add-R "FAIL" "Vault / extension dependency" (($vault | Select-Object -First 3 | ForEach-Object { $_.Filename + ":" + $_.LineNumber }) -join ", ") $true }

# ---- window and resources --------------------------------------------------------------------------------------
try {
    $etNow = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, "Eastern Standard Time")
    $byTime = ($etNow.DayOfWeek -in @("Saturday", "Sunday")) -or ($etNow.Hour -lt 4) -or ($etNow.Hour -ge 20)
    if ($byTime) { Add-R "PASS" "Entry window" ("ET now " + $etNow.ToString("ddd HH:mm") + ": new entries are impossible by time") $true }
    else { Add-R "FAIL" "Entry window" ("ET now " + $etNow.ToString("ddd HH:mm") + " is inside entry hours (Mon-Fri 04:00-20:00 ET); the cutover refuses") $true }
} catch { Add-R "NOT VERIFIED" "Entry window" "could not compute Eastern time" $true }
try { $disk = Get-PSDrive C; $os = Get-CimInstance Win32_OperatingSystem; Add-R "INFO" "Resources" ("disk C free " + [math]::Round($disk.Free / 1GB, 1) + " GB; RAM free " + [math]::Round($os.FreePhysicalMemory / 1MB, 1) + " GB") } catch { Add-R "INFO" "Resources" "NOT VERIFIED" }

# ---- by design (verified in development; not observable on this server before the deploy) -------------------------
Add-R "INFO" "Existing records unchanged by the deploy" "PASS in dev: the cutover adds the trade_entry_lock table + two triggers only; no migration, no UPDATE/DELETE of signals or positions (docs/production_cutover_plan.md)"
Add-R "INFO" "Entries stay locked" "PASS in dev: update.ps1 installs the lock LOCKED, never unlocks; unlock needs release_tool.py unlock after /safety verifies the running build"

# ---- report ------------------------------------------------------------------------------------------------------
# a mandatory check that never ran (a script error) must never read as "nothing wrong"
$required = @("Server commit", "Release on remote", "Working tree", "Service and /health", "Restore-tested dump present", "Restore log", "Validated dump", "Fresh backup", "Database read-only session", "Required database queries", "Paper positions are zero", "Open signals recoverable", "Pooler settings", "Entry window")
foreach ($n in $required) { if (-not ($R | Where-Object { $_.Name -eq $n })) { Add-R "NOT VERIFIED" $n "this check did not complete (script error)" $true } }
if (-not ($R | Where-Object { $_.Name -like "*Vault*" })) { Add-R "NOT VERIFIED" "Vault / extension dependency" "this check did not complete (script error)" $true }
if ($Error.Count -gt 0) { Add-R "INFO" "Script errors" ("" + $Error.Count + " internal error(s) were raised; any check that did not complete is NOT VERIFIED (details withheld so no value can leak)") }
$bad = @($R | Where-Object { $_.Mandatory -and $_.Status -ne "PASS" })
$out = New-Object System.Collections.Generic.List[string]
$out.Add("TRDGO-US read-only pre-deployment preflight  " + (Get-Date -Format o) + "  host " + $env:COMPUTERNAME)
foreach ($x in $R) { $out.Add((Redact ("[{0}] {1}{2}: {3}" -f $x.Status, $x.Name, $(if ($x.Mandatory) { " *" } else { "" }), $x.Detail))) }
$out.Add("")
# GO needs POSITIVE proof: every required check present AND PASS, plus the Vault check; "nothing failed" is not enough
$passedNames = @($R | Where-Object { $_.Mandatory -and $_.Status -eq "PASS" } | ForEach-Object { $_.Name })
$notProven = @($required | Where-Object { $passedNames -notcontains $_ })
$vaultProven = (@($passedNames | Where-Object { $_ -like "*Vault*" }).Count -ge 1)
if ($bad.Count -eq 0 -and $notProven.Count -eq 0 -and $vaultProven -and $R.Count -ge 15) { $out.Add("DECISION: GO (every mandatory * condition PASSED). Nothing was changed. Await explicit approval before deploying.") }
else { $out.Add("DECISION: NO-GO. Not proven: " + ((@($bad | ForEach-Object { $_.Name + " [" + $_.Status + "]" }) + @($notProven | Where-Object { $n = $_; -not ($bad | Where-Object { $_.Name -eq $n }) } | ForEach-Object { $_ + " [no PASS recorded]" })) -join "; ")) }
$out.Add("")
$out.Add("FRESH PRE-DEPLOYMENT DUMP (prepared text only, NOT executed; run only after approval, inside the window, with the book flat):")
$out.Add('  $u=((Get-Content C:\apps\us-stock-reader\backend\.env | Where-Object { $_ -match "^DATABASE_URL=" }) -replace "^DATABASE_URL=","").Trim([char]34,[char]39)')
$out.Add('  $m=[regex]::Match($u,"^[\w\+]+://(?<user>[^:]+):(?<pw>[^@]+)@(?<host>[^:/]+):\d+/(?<db>[^?]+)")')
$out.Add('  $env:PGHOST=$m.Groups["host"].Value; $env:PGPORT="5432"; $env:PGUSER=[uri]::UnescapeDataString($m.Groups["user"].Value); $env:PGPASSWORD=[uri]::UnescapeDataString($m.Groups["pw"].Value); $env:PGDATABASE=$m.Groups["db"].Value; $env:PGSSLMODE="require"')
$out.Add('  & <path to pg_dump 17> -Fc -Z 6 --no-owner --no-privileges --schema=public -f C:\audit-reports\db-backups\trdgo_us_<yyyymmdd-hhmm>_predeploy.dump')
$out.Add('  Remove-Item Env:PGHOST,Env:PGPORT,Env:PGUSER,Env:PGPASSWORD,Env:PGDATABASE,Env:PGSSLMODE')
$out.Add("  (port 5432 = the session pooler: pg_dump cannot use the transaction pooler; the values are never printed)")
$file = Join-Path $rd ("preflight-" + $stamp + ".txt")
Set-Content -Path $file -Value $out -Encoding UTF8
$out | ForEach-Object { Write-Host $_ }
Write-Host ""; Write-Host ("Report saved: " + $file)
'@ | Set-Content (Join-Path $ReportDir "preflight_ro.ps1") -Encoding UTF8
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ReportDir "preflight_ro.ps1")
