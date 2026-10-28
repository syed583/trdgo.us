$ErrorActionPreference = "Stop"
$AR = "@AR@"; $R = "@R@"; $py = "@PY@"; $pg = "@PG@"
# ---- 1. which PostgreSQL 17 tools exist (read-only) ----
$need = "initdb", "pg_ctl", "pg_restore", "psql", "postgres"
$rep = $need | ForEach-Object { [pscustomobject]@{ tool = $_; present = (Test-Path (Join-Path $pg ($_ + ".exe"))) } }
Write-Host ("tool folder: " + $pg); $rep | Format-Table -AutoSize | Out-String | Write-Host
$missing = @($rep | Where-Object { -not $_.present } | ForEach-Object { $_.tool })
if ($missing.Count -gt 0) { Write-Host ("MISSING in " + $pg + ": " + ($missing -join ", ") + " -- nothing was run.") -ForegroundColor Red; return }
Write-Host ((& (Join-Path $pg "pg_restore.exe") --version) + "  /  " + (& (Join-Path $pg "initdb.exe") --version))
# ---- 2. the read-only fingerprint helper (SHA-256 checked) ----
$fpSha = "@FPSHA@"
$fpSrc = @'
@FPSRC@
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
# ---- 5. the scratch restore (isolated local cluster 127.0.0.1:54320; never reads production settings) ----
& $py (Join-Path $AR "phaseC\scratch_restore_test.py") --dump $d.FullName --pg-bin $pg --sql-dir (Join-Path $AR "apply\migrations") --gate (Join-Path $AR "phaseC\prod_gate_checks.py") --work (Join-Path $AR "scratch_restore") --expect-tables 55
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
Write-Host ("scratch folder removed: " + (-not (Test-Path (Join-Path $AR "scratch_restore"))))
