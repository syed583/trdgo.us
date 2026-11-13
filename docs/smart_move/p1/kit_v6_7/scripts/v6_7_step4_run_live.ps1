# TRDGO-US Smart Money Move v5 (kit v5 + collector wrapper P1 installed, P1 enabled in the settings file) - ONE supervised LIVE collection run.
#
# WITHOUT -Confirm 'YES-RUN-LIVE-ONCE' (typed exactly, case-sensitive) this only runs the read-only gates and prints what it would do: no request, no change.
# WITH it, after the gates pass, it starts exactly ONE run of the installed collector wrapper in LIVE mode, for THIS process only:
#   - SMART_MOVE_COLLECTION_LIVE=1 exists only inside this PowerShell process and is removed when the run ends (nothing machine-wide, nothing in .env, nothing in the scheduled task);
#   - the settings file stays Mode=DRY_RUN, the hourly task stays DRY_RUN, no scheduler change, no service restart;
#   - the budget is limited here to the owner-approved 2026-11-05 numbers: -Authorize 1..150 per run, -DailyLimit 1..700 per New York day (the wrapper's design ceilings; it refuses more);
#   - the wrapper reads the settings file (SelectPolicy=deadline_priority, CalendarHorizonDays=30, SelectTop ...), so the LIVE run uses the same P1 plan as the hourly DRY_RUN;
#   - the runner needs your signed attestation (read, never changed; 24 h validity), releases its own kill switch only while it works, and re-engages it afterwards; this script re-creates it if a crash left it released.
param(
  [string]$Confirm = '',
  [string]$App = 'C:\apps\us-stock-reader',
  [string]$Smlive = 'C:\smlive',
  [string]$Task = 'TRDGO-SmartMove-Collector',
  [ValidateRange(1, 500)][int]$Authorize = 150,
  [ValidateRange(1, 700)][int]$DailyLimit = 700,
  [switch]$WithF7,
  [switch]$SkipTask
)
$ErrorActionPreference = 'Continue'
$script:fail = 0
function Say($m) { Write-Host $m }
function Ok($n, $c, $x = '') { if ($c) { Say ('   PASS  ' + $n + $(if ($x) { '  ' + $x })) } else { $script:fail++; Say ('   FAIL  ' + $n + $(if ($x) { '  ' + $x })) } }
function Info($m) { Say ('   INFO  ' + $m) }
function LfSha([string]$p) {
  # SHA-256 of the LF-normalised bytes: exactly what the release manifests and the installers use (a CRLF checkout must not read as a different file)
  if (-not (Test-Path -LiteralPath $p)) { return 'MISSING' }
  $enc = [Text.Encoding]::GetEncoding(28591); $x = $enc.GetBytes($enc.GetString([IO.File]::ReadAllBytes($p)).Replace("`r`n", "`n"))
  return ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($x)) -replace '-', '').ToLower()
}
$armed = ($Confirm -ceq 'YES-RUN-LIVE-ONCE')
$store = "$App\sm_evidence_store"; $stateRoot = "$App\sm_collector_state"; $live = "$stateRoot\live"; $b = "$App\backend"
$settings = "$App\collector_tools\collector.settings.txt"; $attf = "$App\collector_tools\shared_key_attestation.json"; $wrapper = "$App\collector_tools\run_engine20_collection.ps1"
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss'); $report = "$Smlive\step5_run_$stamp.txt"
Say "=== v6 supervised LIVE run  (armed: $armed; budget: $Authorize per run, $DailyLimit per New York day; F7 options capture: $($WithF7.IsPresent))"
Say "=== 1. gates (read-only)"
$kit = [ordered]@{ 'smart_move_collection_runner.py' = '250464de825c4d66c973197e6f19a09f3e23559f26c3a86ae5f8e58f217a9c3e'; 'smart_move_monday_bmo.py' = '5a926530b29417ad246b1a3caa069d8ccfea27f783976bb513be0b99ed28ecbe'; 'smart_move_outcomes.py' = '0572074c4d0cf81f010fc9e07a774ee59efc9a48dea07cd813d32f2e89ffe4ed'; 'smart_move_engine20.py' = '467470c36dd83fc0b229c45e86282e9baa00a47f5ee4001515d1790aefec6ee6'; 'smart_move_live.py' = '8c659ab88f05778559bf01339c9a530bba078c3515b0c28d66fc78d9d31f4270'; 'smart_move_calendar_session.py' = '48addc8e831a560d07b6c55ac4248fce062a0a2e99d0eb580683a5a5b5311573'; 'smart_move_calendar_tiers.py' = '05f2edb87404bc61d704d3e2b3a36d36954b0d761cd88272f6eeda55645ee4e9'; 'smart_move_calendar_changes.py' = 'f7b6bda580e62c408e5a3befb96162e7907ec36222832ecf843b9136657dca2a'; 'smart_move_universe.py' = '3ca98cfef84d07a4ec001a29efe93c8c303ce74ebef08bdd431f45213fbd2cd0'; 'smart_move_live_publisher.py' = 'fb6c0b9f307f0d2fecf2ed59c44513f8370f5078fef644b11c49a633772be606'; 'smart_move_calendar_guard.py' = 'b4d2994ef328a7a432d95f3561e65d5e8f225b7429fd7401f1c9e33002980ec0'; 'smart_move_collection_jobs.py' = '81d8d2d4d7bbcca948c6ff0aaf1cdd941680e7996a26bb21d3172bc22a36e411'; 'smart_move_event_records.py' = '543e38dad350cfb141289fbf236fc62b9f38e690ef4e300d6b77fe42364586ea' }
$kitBad = @(); foreach ($n in $kit.Keys) { if ((LfSha "$b\$n") -ne $kit[$n]) { $kitBad += $n } }
Ok "installed engine = exactly kit v6.7 (v6.4 + market-cap floor 7,000,000 USD in the collector; every protected file unchanged)" ($kitBad.Count -eq 0) ($kitBad -join ', ')
Ok "installed collector wrapper = exactly wrapper v3 (96d2396a...; MaxRun 500)" ((LfSha $wrapper) -eq '96d2396a89e4f5daa9d6a8fb2222fdbf8897e5fe959b1a9b2623964691c79e0c')
Ok "frozen scorer pinned (62e09aa0ed785e2a...)" ((LfSha "$App\docs\smart_move\shadow_impl\smvshadow\scorer.py").StartsWith('62e09aa0ed785e2a'))
$st = if (Test-Path -LiteralPath $settings) { Get-Content -LiteralPath $settings -Raw } else { '' }
Ok "settings file Mode is DRY_RUN (unchanged by this script)" ($st -match '(?m)^\s*Mode\s*=\s*DRY_RUN\s*$')
Ok "settings limits are the approved 500 / 700" (($st -match '(?m)^\s*Authorize\s*=\s*500\s*$') -and ($st -match '(?m)^\s*DailyLimit\s*=\s*700\s*$'))
Ok "settings enable P1 (CalendarHorizonDays=30, SelectPolicy=deadline_priority)" (($st -match '(?m)^\s*CalendarHorizonDays\s*=\s*30\s*$') -and ($st -match '(?m)^\s*SelectPolicy\s*=\s*deadline_priority\s*$'))
Ok "SMART_MOVE_COLLECTION_LIVE is not already set (process, user, machine)" (-not ($env:SMART_MOVE_COLLECTION_LIVE -or [Environment]::GetEnvironmentVariable('SMART_MOVE_COLLECTION_LIVE', 'User') -or [Environment]::GetEnvironmentVariable('SMART_MOVE_COLLECTION_LIVE', 'Machine')))
Ok "runner kill switch present" (Test-Path -LiteralPath "$store\KILL")
Ok "wrapper kill switch absent" (-not (Test-Path -LiteralPath "$stateRoot\KILL"))
Ok "no collector lock and no collector process" ((-not (Test-Path -LiteralPath "$stateRoot\collection.lock")) -and (@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessId -ne $PID -and $_.Name -match '^(powershell|pwsh|python)' -and $_.CommandLine -match 'run_engine20_collection|smart_move_collection_runner' }).Count -eq 0))
$hoursLeft = $null; $att = $null
try { $att = Get-Content -LiteralPath $attf -Raw -Encoding UTF8 | ConvertFrom-Json; $signed = [datetime]::Parse("$($att.attested_at)", [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal).ToUniversalTime(); $hoursLeft = 24 - ((Get-Date).ToUniversalTime() - $signed).TotalHours } catch { }
Ok ("owner attestation valid for at least 1 more hour (hours left: " + $(if ($null -ne $hoursLeft) { [math]::Round($hoursLeft, 1) } else { 'unreadable or missing' }) + ")") ($null -ne $hoursLeft -and $hoursLeft -ge 1)
if ($att) { Ok "attestation names production as the designated collector, allows market hours, states the production cap of 2" (($att.designated_collector -eq 'production') -and ($att.market_hours_ok -eq $true) -and ($att.production_uw_max_inflight -eq 2) -and ($att.acknowledges_shared_token_three_slot_limit -eq $true) -and ($att.acknowledges_production_and_other_app_use_not_visible -eq $true)) }
try { $newest = (Get-Content -LiteralPath "$store\cal_obs.jsonl" | ForEach-Object { ($_ | ConvertFrom-Json).observed_at } | ForEach-Object { [datetime]::Parse($_, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal) } | Measure-Object -Maximum).Maximum.ToUniversalTime(); Info ("newest calendar observation is " + [math]::Round(((Get-Date).ToUniversalTime() - $newest).TotalHours, 2) + " h old (P1 refreshes per slot: near events every 5 h, far events every 24 h, at most 30% of the run budget)") } catch { Info 'calendar observations unreadable' }
Info ("universe.json present: " + (Test-Path -LiteralPath "$live\universe.json") + " (written by any P1 pass, DRY_RUN included)")
$used = $null; try { $used = (Get-Content -LiteralPath "$live\live_status.json" -Raw | ConvertFrom-Json).requests.used_today } catch { }
Info ("requests already used today per the status file: " + $(if ($null -ne $used) { $used } else { 'unknown' }) + " (the governor counts attempts in the store; the day resets at midnight New York)")
if (-not $SkipTask) {
  $t = Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
  if ($t) { $ti = Get-ScheduledTaskInfo -TaskName $Task; $mins = ($ti.NextRunTime - (Get-Date)).TotalMinutes
    Ok "hourly task is Ready, does not pass -Live" (($t.State -eq 'Ready') -and ((($t.Actions | ForEach-Object { $_.Arguments }) -join ' ') -notmatch '(?i)-Live'))
    Ok ("next hourly trigger is " + [math]::Round($mins, 1) + " min away (need at least 12; the run holds the collector lock, so a trigger during it would only skip)") ($mins -ge 12) } else { Ok "scheduled task exists" $false }
}
if ($script:fail -gt 0) { Say ''; Say ("GATES FAILED (" + $script:fail + "): nothing was started or changed."); exit 3 }
if (-not $armed) { $runCmd = "powershell -NoProfile -ExecutionPolicy Bypass -File $Smlive\v6_7_step4_run_live.ps1 -Authorize $Authorize -DailyLimit $DailyLimit" + $(if ($WithF7) { ' -WithF7' } else { '' }) + " -Confirm YES-RUN-LIVE-ONCE"; Say ''; Say "All gates PASS. PLAN ONLY (nothing started). To run it for real (only after the owner's explicit approval), the EXACT command with these budget parameters is:"; Say "  $runCmd"; exit 0 }

Start-Transcript -Path $report -Force | Out-Null
$code = 99
try {
  Say "=== 2. ONE supervised LIVE run (process-only live flag)"
  $env:SMART_MOVE_COLLECTION_LIVE = '1'
  $wargs = @('-SettingsFile', $settings, '-Live', '-Attestation', $attf, '-Authorize', $Authorize, '-DailyLimit', $DailyLimit); if ($WithF7) { $wargs += '-WithF7' }
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $wrapper @wargs 2>&1 | ForEach-Object { Say ('   ' + [string]$_) }
  $code = $LASTEXITCODE
}
finally {
  Remove-Item Env:SMART_MOVE_COLLECTION_LIVE -ErrorAction SilentlyContinue
  Say "=== 3. close-out"
  Say ('   process live flag removed: ' + (-not $env:SMART_MOVE_COLLECTION_LIVE))
  if (-not (Test-Path -LiteralPath "$store\KILL")) { [IO.File]::WriteAllText("$store\KILL", "re-engaged by v6_7_step4_run_live.ps1 after the run (the runner did not leave it engaged)"); Say '   WARNING: the runner kill switch was missing after the run; it has been re-created.' } else { Say '   runner kill switch present again: True' }
  Say ('   wrapper kill switch absent: ' + (-not (Test-Path -LiteralPath "$stateRoot\KILL")) + ' | collector lock released: ' + (-not (Test-Path -LiteralPath "$stateRoot\collection.lock")))
  Say ('   settings file Mode still DRY_RUN: ' + ((Get-Content -LiteralPath $settings | Where-Object { $_ -match '^\s*Mode\s*=\s*DRY_RUN\s*$' }).Count -eq 1))
  Say ('   universe.json present: ' + (Test-Path -LiteralPath "$live\universe.json"))
}
Say ''
Say ("=== RUN FINISHED with exit code $code  (0 = completed; 2 = wrapper blocked (limits/attestation/paths); 6 = blocked by a live gate; 7 = shared-key attestation; 8 = ledger integrity; other = see console)")
Say "Next, the read-only report (send me its whole output):"
Say "  $App\backend\.venv\Scripts\python.exe -I $Smlive\step4_report_v3.py"
Say "Transcript saved: $report"
Stop-Transcript | Out-Null
exit $code
