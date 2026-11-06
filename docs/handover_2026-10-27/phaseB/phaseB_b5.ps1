# TRDGO-US Phase B / stage B5: run the corrected, installed, hash-verified READ-ONLY preflight and prove it changed nothing in production.
# The preflight itself: read-only git, /health, one read-only database transaction per query, file inspection. It never starts a dump.
# Files it writes in $ReportDir (identified at the end): preflight_ro.py, preflight_ro.ps1 (generated helpers) and preflight-<stamp>.txt (the report).
# Existing preflight_ro.py / preflight_ro.ps1 are RENAMED to .old-<stamp> first, never overwritten or deleted.
param([string]$Root = "C:\apps\us-stock-reader", [string]$ReportDir = "C:\audit-reports", [string]$Svc = "USStockReader", [int]$Port = 8000,
      [string]$BaseSha = "36094e89ff78c531bd6c8997208d31f02f1aafee",
      [string]$PreflightPath = "C:\audit-reports\vps_predeploy_preflight_277f30f.ps1",
      [string]$ExpectedSha = "481C3F7507F2D68204472380EC57D28719B13519BCA07D92E5B802243F4674B7")
$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
function G { param([Parameter(ValueFromRemainingArguments = $true)]$a) $o = & git --no-optional-locks -C $Root @a 2>&1; return @($o) }
function State {
    $s = Get-CimInstance Win32_Service -Filter ("Name='" + $Svc + "'")
    $h = $null; try { $h = (Invoke-WebRequest -Uri ("http://127.0.0.1:" + $Port + "/health") -UseBasicParsing -TimeoutSec 8).Content } catch { }
    [pscustomobject]@{ Head = ((G rev-parse HEAD) -join "").Trim(); Dirty = @(G status --porcelain | Where-Object { $_ }).Count; Branch = ((G branch --show-current) -join "").Trim()
        Stash = @(G stash list | Where-Object { $_ }).Count; State = $s.State; Pid = $s.ProcessId; Healthy = [bool]($h -and $h -match '"status"\s*:\s*"ok"')
        Started = (Get-Process -Id $s.ProcessId -ErrorAction SilentlyContinue).StartTime }
}
function Stop-Here([string]$why) { Write-Host ("STOPPED, NOTHING WAS RUN: " + $why) -ForegroundColor Red; exit 2 }
function Listing { Get-ChildItem $ReportDir -File -Recurse -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName + "|" + $_.Length + "|" + $_.LastWriteTimeUtc.Ticks } }

# 1. the script about to run is exactly the verified one
if (-not (Test-Path $PreflightPath)) { Stop-Here ("not found: " + $PreflightPath) }
$have = (Get-FileHash -LiteralPath $PreflightPath -Algorithm SHA256).Hash
if ($have -ne $ExpectedSha) { Stop-Here ("SHA-256 of the preflight is " + $have + ", expected " + $ExpectedSha) }
Write-Host ("[PASS] preflight SHA-256 verified: " + $have)

# 2. state before
$b = State
Write-Host ("before: HEAD " + $b.Head + "  branch " + $b.Branch + "  changes " + $b.Dirty + "  stashes " + $b.Stash + "  service " + $b.State + " pid " + $b.Pid + "  health " + $b.Healthy)
if ($b.Head -ne $BaseSha) { Stop-Here "HEAD is not the audited base" }
$before = @(Listing)

# 3. preserve the generated helpers of the earlier run (rename, keep)
foreach ($n in "preflight_ro.py", "preflight_ro.ps1") {
    $p = Join-Path $ReportDir $n
    if (Test-Path $p) { Rename-Item -LiteralPath $p -NewName ($n + ".old-" + $stamp); Write-Host ("kept previous helper: " + $n + ".old-" + $stamp) }
}

# 4. run the preflight (its own report file is written in $ReportDir)
Write-Host ""; Write-Host "=================== PREFLIGHT OUTPUT ==================="
& powershell -NoProfile -ExecutionPolicy Bypass -File $PreflightPath
$pfExit = $LASTEXITCODE
Write-Host "=================== END PREFLIGHT ===================="; Write-Host ("preflight process exit code: " + $pfExit)

# 5. state after, and every file that appeared or changed
$a = State
Write-Host ""
function Chk([bool]$c, [string]$what) { if ($c) { Write-Host ("[PASS] " + $what) } else { Write-Host ("[FAIL] " + $what) -ForegroundColor Red; $script:bad = $true } }
$bad = $false
Chk ($a.Head -eq $b.Head -and $a.Branch -eq $b.Branch) ("HEAD and branch unchanged: " + $a.Head + " / " + $a.Branch)
Chk ($a.Dirty -eq 0) ("working tree still completely clean (changes: " + $a.Dirty + ")")
Chk ($a.Stash -eq $b.Stash) ("stash untouched (" + $a.Stash + " stash entry): the five files were not restored")
Chk ($a.State -eq "Running" -and $a.Healthy -and $a.Pid -eq $b.Pid -and $a.Started -eq $b.Started) ("service still Running, same pid and start time (not restarted), /health ok = " + $a.Healthy)
$after = @(Listing)
$new = @($after | Where-Object { $before -notcontains $_ })
Write-Host ""; Write-Host ("files created or changed in " + $ReportDir + " by this stage:")
foreach ($l in $new) { $x = $l -split "\|"; Write-Host ("  " + $x[0] + "  (" + $x[1] + " bytes)") }
$tmp = @(Get-ChildItem $ReportDir -Filter "preflight_*.tmp" -File -ErrorAction SilentlyContinue)
Write-Host ("leftover temporary files (preflight_*.tmp): " + $tmp.Count)
$rep = Get-ChildItem $ReportDir -Filter "preflight-*.txt" -File | Sort-Object LastWriteTime | Select-Object -Last 1
if ($rep) { Write-Host ("report file: " + $rep.FullName) }
if ($bad) { Write-Host "B5: a production-state check FAILED after the preflight. Send me this output." -ForegroundColor Red; exit 4 }
