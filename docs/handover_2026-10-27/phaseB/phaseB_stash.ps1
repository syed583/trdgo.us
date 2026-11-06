param([string]$Root = "C:\apps\us-stock-reader", [string]$ReportDir = "C:\audit-reports",
      [string]$BaseSha = "36094e89ff78c531bd6c8997208d31f02f1aafee")
# TRDGO-US Phase B / stage B2: put the five known local files aside with a REVERSIBLE git stash.
# Nothing is deleted. It refuses (and changes nothing) unless the tree is EXACTLY the five known items on the audited base commit.
# Recovery instructions are printed at the end and are NOT executed.
$ErrorActionPreference = "Continue"      # git writes warnings to stderr; success is judged by exit codes and explicit checks
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$msg = "local files put aside before the 2026-10-27 release (5 items)"
$known = @("Data_Health_Check.xlsx", "backend/diag.py", "backend/diag2.py", "backend/diag3.py", "backend/diag_claude.py")
function Stop-Here([string]$why) { Write-Host ("STOPPED, NOTHING WAS CHANGED: " + $why) -ForegroundColor Red; exit 2 }
function G { param([Parameter(ValueFromRemainingArguments = $true)]$a) $o = & git --no-optional-locks -C $Root @a 2>&1; return @($o) }
function Sha([string]$p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash }

# ---- 1. preconditions (read-only) ------------------------------------------------------------------------------------------
$head = ((G rev-parse HEAD) -join "").Trim()
if ($head -ne $BaseSha) { Stop-Here ("HEAD is " + $head + ", not the audited base " + $BaseSha) }
$st = @(G status --porcelain | Where-Object { $_ })
$paths = @($st | ForEach-Object { ("" + $_).Substring(3).Trim() })
$extra = @($paths | Where-Object { $known -notcontains $_ }); $missing = @($known | Where-Object { $paths -notcontains $_ })
if ($extra.Count -or $missing.Count -or $st.Count -ne 5) { Stop-Here ("the tree is not exactly the five known items. Unexpected: [" + ($extra -join ", ") + "]  Missing: [" + ($missing -join ", ") + "]") }
if (@(G stash list | Where-Object { $_ -match [regex]::Escape($msg) }).Count) { Stop-Here "a stash with this message already exists" }
foreach ($f in $known) {
    try { $fs = [System.IO.File]::Open((Join-Path $Root $f), "Open", "Read", "Read"); $fs.Close() } catch { Stop-Here ("cannot read " + $f + " (open in another program?): " + $_.Exception.Message) }
}
$branchBefore = ((G branch --show-current) -join "").Trim()
$ignoredBefore = @(G status --ignored --porcelain | Where-Object { $_ -like "!!*" } | Sort-Object)
$envHashBefore = $null; $envPath = Join-Path $Root "backend\.env"; if (Test-Path $envPath) { $envHashBefore = Sha $envPath }
Write-Host "preconditions OK: audited base commit, exactly the five known items, files readable"

# ---- 2. backup copies BEFORE any change, verified byte for byte ------------------------------------------------------
$bk = Join-Path $ReportDir ("server-local-files-" + $stamp)
$null = New-Item -ItemType Directory -Force -Path $bk
$lines = @()
foreach ($f in $known) {
    $src = Join-Path $Root $f; $dst = Join-Path $bk (Split-Path $f -Leaf)
    Copy-Item -LiteralPath $src -Destination $dst
    $a = Sha $src; $b = Sha $dst
    if ($a -ne $b) { Stop-Here ("the backup copy of " + $f + " does not match (nothing was stashed)") }
    $lines += ($a + "  " + $f)
}
Set-Content -Path (Join-Path $bk "SHA256.txt") -Value $lines -Encoding ASCII
Write-Host ("backup copies verified: " + $bk)

# ---- 3. the stash (the only change; reversible) ------------------------------------------------------------------------------
$out = G stash push --include-untracked -m $msg
if ($LASTEXITCODE -ne 0) { Write-Host ("git stash failed: " + ($out -join " | ")) -ForegroundColor Red; exit 3 }

# ---- 4. verification ---------------------------------------------------------------------------------------------------------
$ok = $true
function Check([bool]$c, [string]$what) { if ($c) { Write-Host ("[PASS] " + $what) } else { Write-Host ("[FAIL] " + $what) -ForegroundColor Red; $script:ok = $false } }
Check (@(G status --porcelain | Where-Object { $_ }).Count -eq 0) "git status --porcelain prints nothing: the working tree is clean"
$sl = @(G stash list | Where-Object { $_ })
Check ($sl.Count -eq 1 -and ($sl[0] -match [regex]::Escape($msg))) ("exactly one stash exists: " + ($sl -join " | "))
$names = @(G stash show --include-untracked --name-only "stash@{0}" | Where-Object { $_ } | Sort-Object)
Check (($names -join ",") -eq (($known | Sort-Object) -join ",")) ("the stash holds exactly the five files: " + ($names -join ", "))
Check (-not (Test-Path (Join-Path $Root "backend\diag.py")) -and -not (Test-Path (Join-Path $Root "backend\diag2.py")) -and -not (Test-Path (Join-Path $Root "backend\diag3.py")) -and -not (Test-Path (Join-Path $Root "backend\diag_claude.py"))) "the four diag scripts are out of the working folder"
$committed = ((G rev-parse "HEAD:Data_Health_Check.xlsx") -join "").Trim(); $nowBlob = ((G hash-object --no-filters (Join-Path $Root "Data_Health_Check.xlsx")) -join "").Trim()
Check ($committed -eq $nowBlob) "Data_Health_Check.xlsx is the committed version again"
$ignoredAfter = @(G status --ignored --porcelain | Where-Object { $_ -like "!!*" } | Sort-Object)
Check (($ignoredAfter -join "|") -eq ($ignoredBefore -join "|")) ("ignored files (.env, .env.bak, .env.seoul_*, venv, logs ...) are exactly as before: " + $ignoredAfter.Count + " entries")
if ($envHashBefore) { Check ((Sha $envPath) -eq $envHashBefore) "backend\.env is byte-identical to before" }
Check (((G rev-parse HEAD) -join "").Trim() -eq $BaseSha) "HEAD is unchanged"
Check (((G branch --show-current) -join "").Trim() -eq $branchBefore) ("branch is unchanged: " + $branchBefore)
$recorded = @{}; foreach ($l in $lines) { $recorded[($l -split "  ", 2)[1]] = ($l -split "  ", 2)[0] }
$bad = @($known | Where-Object { (Sha (Join-Path $bk (Split-Path $_ -Leaf))) -ne $recorded[$_] })
Check ($bad.Count -eq 0) "the five external backup copies still match the hashes recorded before the stash"
Check ((Get-ChildItem $bk -File).Count -eq 6) ("backup folder holds the five files and SHA256.txt: " + $bk)
Write-Host ""
if ($ok) { Write-Host "STASH DONE AND VERIFIED." -ForegroundColor Green } else { Write-Host "STASH DONE BUT A CHECK FAILED: do not continue; send me this output. To undo: see below." -ForegroundColor Red }
Write-Host ""
Write-Host "RECOVERY (not executed):"
Write-Host "  1. restore all five exactly where they were:   git -C $Root stash pop"
Write-Host "     (keeps a copy in the stash if you prefer:    git -C $Root stash apply )"
Write-Host "  2. or copy them back by hand from $bk and verify with SHA256.txt (these are the BYTE-EXACT originals: git may convert line endings of the"
Write-Host "     small text files on a stash round trip (core.autocrlf), so compare against SHA256.txt and, if only line endings differ, copy the originals back)"
Write-Host "  3. verify:  git -C $Root status --porcelain   (should list the five again)"
Write-Host "  NOTE: after the cut-over, restoring them makes the tree dirty again: a later service restart would report it and the unlock check would refuse."
if (-not $ok) { exit 4 }
