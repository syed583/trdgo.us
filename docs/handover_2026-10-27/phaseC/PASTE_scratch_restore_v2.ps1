$ErrorActionPreference = "Stop"
$AR = "C:\audit-reports"; $R = "C:\apps\us-stock-reader"; $py = "C:\apps\us-stock-reader\backend\.venv\Scripts\python.exe"; $pg = "C:\Program Files\PostgreSQL\17\bin"
# ---- 0. install the hardened scratch test (SHA-256 checked) ----
$srSha = "4651679013c940d628ae8a8881f6a0f299744c22e93e7c13b8f584c121dee410"
$srB64 = @'
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
bXQIV1llau38L7p/2j3hSAAA
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
