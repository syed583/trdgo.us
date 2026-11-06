# TRDGO-US Phase B / stages B3+B4: fetch ONLY the release branch, install three verified scripts into C:\audit-reports (never executed),
# prove the production tree/service were not affected, and review the INTRADAY_MODEL entries of backend\.env read-only (no other key is read or shown).
# Requires install_verified.ps1 in the same folder. Changes: git objects + one remote ref (fetch), new files in $ReportDir. Nothing else.
param([string]$Root = "C:\apps\us-stock-reader", [string]$ReportDir = "C:\audit-reports", [string]$Svc = "USStockReader", [int]$Port = 8000,
      [string]$BaseSha = "36094e89ff78c531bd6c8997208d31f02f1aafee", [string]$Commit = "277f30f849051471745f03b0b5401737fc5392d0",
      [string]$Branch = "release/trdgo-us-safety-20261026")
$ErrorActionPreference = "Continue"
$installer = Join-Path $PSScriptRoot "install_verified.ps1"
$Res = New-Object System.Collections.ArrayList
function Add-Res([string]$s, [string]$what) { [void]$Res.Add([pscustomobject]@{ S = $s; W = $what }); $c = @{ PASS = "Green"; FAIL = "Red"; INFO = "Gray" }[$s]; Write-Host ("[" + $s + "] " + $what) -ForegroundColor $c }
function G { param([Parameter(ValueFromRemainingArguments = $true)]$a) $o = & git --no-optional-locks -C $Root @a 2>&1; return @($o) }
function State {
    $s = Get-CimInstance Win32_Service -Filter ("Name='" + $Svc + "'")
    $h = $null; try { $h = (Invoke-WebRequest -Uri ("http://127.0.0.1:" + $Port + "/health") -UseBasicParsing -TimeoutSec 8).Content } catch { }
    [pscustomobject]@{ Head = ((G rev-parse HEAD) -join "").Trim(); Dirty = @(G status --porcelain | Where-Object { $_ }).Count; Branch = ((G branch --show-current) -join "").Trim()
        State = $s.State; Pid = $s.ProcessId; Healthy = [bool]($h -and $h -match '"status"\s*:\s*"ok"'); Started = (Get-Process -Id $s.ProcessId -ErrorAction SilentlyContinue).StartTime }
}
function Stop-Here([string]$why) { Write-Host ("STOPPED: " + $why) -ForegroundColor Red; exit 2 }
if (-not (Test-Path $installer)) { Stop-Here ("install_verified.ps1 is not next to this script in " + $PSScriptRoot) }

# ---- 1. before ---------------------------------------------------------------------------------------------------------------
$b = State
Add-Res ($(if ($b.Head -eq $BaseSha) { "PASS" } else { "FAIL" })) ("HEAD is " + $b.Head + " (expected " + $BaseSha + "), branch " + $b.Branch)
Add-Res ($(if ($b.Dirty -eq 0) { "PASS" } else { "FAIL" })) ("working tree is clean before (changes: " + $b.Dirty + ")")
Add-Res ($(if ($b.State -eq "Running" -and $b.Healthy) { "PASS" } else { "FAIL" })) ("service " + $b.State + " (pid " + $b.Pid + "), /health ok = " + $b.Healthy)
if (@($Res | Where-Object { $_.S -eq "FAIL" }).Count) { Stop-Here "a precondition failed: nothing was fetched or installed" }

# ---- 2. fetch ONLY the release branch ---------------------------------------------------------------------------------------------
$null = & git -C $Root fetch --no-tags origin $Branch 2>&1
if ($LASTEXITCODE -ne 0) { Stop-Here "git fetch failed (network or credentials). Nothing installed." }
$fh = ((G rev-parse FETCH_HEAD) -join "").Trim()
if ($fh -ne $Commit) { Stop-Here ("the fetched release tip is " + $fh + ", not " + $Commit + ". Nothing installed.") }
Add-Res "PASS" ("fetched only " + $Branch + "; its tip is exactly " + $fh)

# ---- 3. install the three scripts (each: git blob id + SHA-256 + parse check; existing different files are renamed, kept) -----------
foreach ($n in "preflight", "backup", "update") {
    Write-Host ""; Write-Host ("--- installing: " + $n)
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installer -Name $n -Root $Root -ReportDir $ReportDir -Commit $Commit -NoFetch
    $code = $LASTEXITCODE
    Add-Res ($(if ($code -eq 0) { "PASS" } else { "FAIL" })) ("install " + $n + " (exit " + $code + ")")
    if ($code -ne 0) { Stop-Here ("install of " + $n + " failed; stopping before the remaining ones") }
}
Write-Host ""; Write-Host "files in the report folder (existing scripts are kept; renamed ones end in .old-<stamp>):"
Get-ChildItem $ReportDir -File -Filter "*.ps1*" | Sort-Object Name | ForEach-Object { Write-Host ("  {0,-58} {1,8} bytes  {2}" -f $_.Name, $_.Length, (Get-FileHash $_.FullName -Algorithm SHA256).Hash.Substring(0, 16)) }

# ---- 4. after ---------------------------------------------------------------------------------------------------------------------
Write-Host ""
$a = State
Add-Res ($(if ($a.Head -eq $b.Head -and $a.Branch -eq $b.Branch) { "PASS" } else { "FAIL" })) ("HEAD and branch unchanged: " + $a.Head + " / " + $a.Branch)
Add-Res ($(if ($a.Dirty -eq 0) { "PASS" } else { "FAIL" })) ("working tree still clean (changes: " + $a.Dirty + ")")
Add-Res ($(if ($a.State -eq "Running" -and $a.Healthy -and $a.Pid -eq $b.Pid -and $a.Started -eq $b.Started) { "PASS" } else { "FAIL" })) ("service still Running, same pid " + $a.Pid + " and start time (not restarted), /health ok = " + $a.Healthy)

# ---- 5. INTRADAY_MODEL review (reads only lines for this one key) ---------------------------------------------------------------
Write-Host ""; Write-Host "--- INTRADAY_MODEL review (read-only; no other key is read or printed)"
$envFile = Join-Path $Root "backend\.env"
$hits = @(); $n = 0
foreach ($l in [IO.File]::ReadAllLines($envFile)) { $n++; if ($l -match '^\s*(export\s+)?INTRADAY_MODEL\s*=') { $hits += [pscustomobject]@{ Line = $n; Text = $l.Trim() } } }
foreach ($h in $hits) { Write-Host ("  .env line {0}: {1}" -f $h.Line, $h.Text) }
$vals = @($hits | ForEach-Object { (($_.Text -replace '^[^=]*=', '') -replace '\s+#.*$', '').Trim().Trim('"').Trim("'").ToLower() })
$uniq = @($vals | Select-Object -Unique)
Add-Res "INFO" ("entries in .env: " + $hits.Count + "; values: " + ($vals -join ", ") + "; " + $(if ($uniq.Count -le 1) { "IDENTICAL" } else { "DIFFERENT" }))
foreach ($sc in "Machine", "User", "Process") { $v = [Environment]::GetEnvironmentVariable("INTRADAY_MODEL", $sc); Add-Res "INFO" ("environment variable INTRADAY_MODEL, " + $sc + " scope: " + $(if ($v) { $v } else { "(not set)" })) }
$py = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (Test-Path $py) {
    $code = "from dotenv import dotenv_values; print(dotenv_values(r'" + $envFile + "').get('INTRADAY_MODEL'))"
    $eff = ((& $py -c $code 2>&1) -join " ").Trim()
    Add-Res "INFO" ("the application's own dotenv library reads the file value as: " + $eff + "  (a duplicate key: the LAST line wins)")
    $m = [Environment]::GetEnvironmentVariable("INTRADAY_MODEL", "Machine")
    $final = if ($m) { $m } else { $eff }
    Add-Res "INFO" ("EFFECTIVE value for the service: " + $final + "  (" + $(if ($m) { "a machine variable overrides the file" } else { "no machine variable, so the .env value applies" }) + "); the code treats exactly 'v2' as v2 and anything else as v1")
}
Write-Host ""
$fails = @($Res | Where-Object { $_.S -eq "FAIL" }).Count
if ($fails) { Write-Host ("B3/B4: " + $fails + " FAIL. Do not continue; send me this output.") -ForegroundColor Red; exit 4 } else { Write-Host "B3/B4 DONE AND VERIFIED. Nothing was executed except git fetch and file installs." -ForegroundColor Green }
