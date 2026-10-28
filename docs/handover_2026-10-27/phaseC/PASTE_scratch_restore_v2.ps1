$ErrorActionPreference = "Stop"
$AR = "C:\audit-reports"; $R = "C:\apps\us-stock-reader"; $py = "C:\apps\us-stock-reader\backend\.venv\Scripts\python.exe"; $pg = "C:\Program Files\PostgreSQL\17\bin"
# ---- 0. install the hardened scratch test (SHA-256 checked) ----
$srSha = "2b33dfb0786e44867a1b144ccfaed8b0de0ed6c808de5775e37b6c42a541ac25"
$srB64 = @'
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
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
