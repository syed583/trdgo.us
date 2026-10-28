$ErrorActionPreference = "Stop"
$AR = "C:\audit-reports"; $R = "C:\apps\us-stock-reader"; $py = "C:\apps\us-stock-reader\backend\.venv\Scripts\python.exe"; $pg = "C:\Program Files\PostgreSQL\17\bin"
# ---- 0. install the hardened scratch test (SHA-256 checked) ----
$srSha = "537c3e75072a08f42fbd1fa9ebd803d34e5386b801470c48ea033dd813b7c5a2"
$srB64 = @'
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
9Wk6VWkUaaNDuMoLUdbW/wCfjuDw1VoAAA==
'@
$gz = [Convert]::FromBase64String(($srB64 -replace "\s", ""))
$ms = New-Object IO.MemoryStream(,$gz); $gs = New-Object IO.Compression.GZipStream($ms, [IO.Compression.CompressionMode]::Decompress); $out = New-Object IO.MemoryStream; $gs.CopyTo($out)
$srBytes = $out.ToArray()
$h0 = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($srBytes)).Replace("-", "").ToLower()
if ($h0 -ne $srSha) { Write-Host ("STOPPED: scratch test altered while pasting (" + $h0 + ")") -ForegroundColor Red; return }
[IO.File]::WriteAllBytes((Join-Path $AR "phaseC\scratch_restore_test.py"), $srBytes)
Write-Host ("scratch_restore_test.py installed, SHA-256 " + $h0)
# ---- 1. which PostgreSQL 17 tools exist (read-only) ----
$need = "initdb", "pg_ctl", "pg_restore", "psql", "postgres"
$rep = $need | ForEach-Object { [pscustomobject]@{ tool = $_; present = (Test-Path (Join-Path $pg ($_ + ".exe"))) } }
Write-Host ("tool folder: " + $pg); $rep | Format-Table -AutoSize | Out-String | Write-Host
$missing = @($rep | Where-Object { -not $_.present } | ForEach-Object { $_.tool })
if ($missing.Count -gt 0) { Write-Host ("MISSING in " + $pg + ": " + ($missing -join ", ") + " -- nothing was run.") -ForegroundColor Red; return }
Write-Host ((& (Join-Path $pg "pg_restore.exe") --version) + "  /  " + (& (Join-Path $pg "initdb.exe") --version))
# ---- 2. the read-only fingerprint helper (SHA-256 checked) ----
$fpSha = "ea2b54fe5cf0d868ceee98ad1669cd62828a673b2da98ba88962e89b945078e8"
$fpSrc = @'
"""READ-ONLY structural fingerprint of the production database, to prove that a test (e.g. the scratch restore) changed nothing.
One READ ONLY connection, SELECTs only, nothing is printed about credentials. Compare two runs: every key except the *_rows / max_id ones
must be identical; demo_trades row count may only grow (the paper tracker keeps trading).

    python prod_fingerprint.py [C:\\apps\\us-stock-reader]  ->  one JSON line on stdout
"""
import hashlib
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\apps\us-stock-reader")
url = (os.environ.get("DATABASE_URL") or "").strip()
if not url:
    env = root / "backend" / ".env"
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines() if env.exists() else []:
        if line.strip().startswith("DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
if not url:
    sys.exit("STOPPED: no DATABASE_URL")
import psycopg

conn = psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1), connect_timeout=15, prepare_threshold=None)
conn.read_only = True
cur = conn.cursor()
cur.execute("SET TRANSACTION READ ONLY")


def one(sql):
    cur.execute(sql)
    return cur.fetchone()[0]


def md5(rows):
    return hashlib.md5("|".join(str(r) for r in rows).encode()).hexdigest()


tables = [r[0] for r in cur.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1").fetchall()]
cols = [tuple(r) for r in cur.execute("SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, ordinal_position").fetchall()]
idx = [r[0] for r in cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1").fetchall()]
out = {
    "public_tables": len(tables), "table_names_md5": md5(tables), "all_columns_md5": md5(cols), "indexes_md5": md5(idx), "indexes": len(idx),
    "audit_named_objects": one("SELECT COUNT(*) FROM pg_class WHERE relname ILIKE '%intraday_signal_audit%'"),
    "scratch_named_databases": one("SELECT COUNT(*) FROM pg_database WHERE datname ILIKE '%restore_check%'"),
    "roles": one("SELECT COUNT(*) FROM pg_roles"), "role_attributes_md5": md5(cur.execute("SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolcanlogin FROM pg_roles ORDER BY 1").fetchall()),
    "policies": one("SELECT COUNT(*) FROM pg_policies"), "rls_tables": one("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity"),
    "event_triggers": one("SELECT COUNT(*) FROM pg_event_trigger"),
    "demo_trades_acl_md5": md5(cur.execute("SELECT relacl::text FROM pg_class WHERE relname = 'demo_trades' AND relkind = 'r'").fetchall()),
    "demo_trades_rows": one("SELECT COUNT(*) FROM demo_trades"), "demo_trades_max_id": one("SELECT COALESCE(MAX(id), 0) FROM demo_trades"),
}
conn.rollback()
conn.close()
print(json.dumps(out, sort_keys=True))
'@
$fpText = ($fpSrc -replace "`r`n", "`n") + "`n"
$fpBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($fpText)
$h = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($fpBytes)).Replace("-", "").ToLower()
if ($h -ne $fpSha) { Write-Host ("STOPPED: fingerprint helper altered while pasting (" + $h + ")") -ForegroundColor Red; return }
$fp = Join-Path $AR "phaseC\prod_fingerprint.py"; [IO.File]::WriteAllBytes($fp, $fpBytes)
# ---- 3. the newest validated dump and its SHA-256 ----
$d = Get-ChildItem (Join-Path $AR "db-backups") -Filter "*_predeploy.dump" | Sort-Object LastWriteTime | Select-Object -Last 1
$sha = (Get-FileHash $d.FullName -Algorithm SHA256).Hash.ToLower()
$side = ((Get-Content ($d.FullName + ".sha256") -Raw).Trim() -split "\s+")[0].ToLower()
Write-Host ("dump: " + $d.Name + "  " + [math]::Round($d.Length / 1MB, 1) + " MB  SHA-256 " + $sha + "  sidecar match: " + ($sha -eq $side))
if ($sha -ne $side) { Write-Host "STOPPED: the dump does not match its recorded SHA-256" -ForegroundColor Red; return }
# ---- 4. production fingerprint BEFORE (read-only) ----
$beforeText = & $py $fp $R
Write-Host ("production BEFORE: " + $beforeText)
# ---- 5. the scratch restore (isolated local cluster 127.0.0.1, first bindable port from 54320; never reads production settings) ----
& $py (Join-Path $AR "phaseC\scratch_restore_test.py") --dump $d.FullName --pg-bin $pg --sql-dir (Join-Path $AR "apply\migrations") --gate (Join-Path $AR "phaseC\prod_gate_checks.py") --diag-dir (Join-Path $AR "scratch_diag") --expect-tables 55
$rc = $LASTEXITCODE
Write-Host ("scratch restore test exit code: " + $rc)
# ---- 6. production fingerprint AFTER, and the comparison ----
$afterText = & $py $fp $R
Write-Host ("production AFTER:  " + $afterText)
$b = $beforeText | ConvertFrom-Json; $a = $afterText | ConvertFrom-Json
$same = $true
foreach ($p in $b.PSObject.Properties) {
    if ($p.Name -in @("demo_trades_rows", "demo_trades_max_id")) { continue }
    if ($p.Value -ne $a.($p.Name)) { Write-Host ("CHANGED: " + $p.Name + "  " + $p.Value + " -> " + $a.($p.Name)) -ForegroundColor Red; $same = $false }
}
$grew = ($a.demo_trades_rows -ge $b.demo_trades_rows)
Write-Host ("production structure unchanged (tables, columns, indexes, roles, policies, RLS, ACL, no audit objects, no scratch database): " + $same)
Write-Host ("demo_trades rows " + $b.demo_trades_rows + " -> " + $a.demo_trades_rows + "  (never fewer: " + $grew + ")")
Write-Host ("scratch folder removed: " + (-not (Test-Path (Join-Path $env:TEMP "trdgo_scratch_restore"))))
