# TRDGO-US Smart Money Move v5 - SCHEDULED LIVE collection run (owner decision 2026-11-05: LIVE every 6 hours, 500 per run (since 2026-11-11) / 700 per New York day).
# Non-interactive: runs the same read-only gates as v6_7_step4_run_live.ps1; if ANY gate fails the run is SKIPPED and the reason is logged (fail closed). It never changes the settings file, the hourly
# DRY_RUN task, .env or the limits. The live flag exists only inside this process. Stop switch: create C:\smlive\LIVE_SCHEDULE_KILL and every scheduled LIVE run skips until the file is deleted.
# Attestation: the owner's 24 h acknowledgement (collector_tools\shared_key_attestation.json) is read, never written; when it has expired the run skips (renew it with the one-line command).
param(
  [string]$App = 'C:\apps\us-stock-reader',
  [string]$Smlive = 'C:\smlive',
  [string]$Task = 'TRDGO-SmartMove-Collector',
  [Parameter(Mandatory = $true)][ValidateRange(1, 500)][int]$Authorize,
  [Parameter(Mandatory = $true)][ValidateRange(1, 700)][int]$DailyLimit,
  [string]$PilotApproved = '',
  [string]$SelfTask = 'TRDGO-SmartMove-LIVE-6h',
  [switch]$WithF7,
  [switch]$PlanOnly
)
$ErrorActionPreference = 'Continue'
$script:fail = 0; $script:why = @()
$logDir = "$Smlive\live_schedule"; New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss'); $report = "$logDir\sched_run_$stamp.txt"; $jlog = "$logDir\scheduled_live_runs.jsonl"
Start-Transcript -Path $report -Force | Out-Null
function Say($m) { Write-Host $m }
function Jl([hashtable]$o) { $o.at = (Get-Date).ToUniversalTime().ToString('o'); Add-Content -LiteralPath $jlog -Value ($o | ConvertTo-Json -Compress) -Encoding ascii }
function Ok($n, $c, $x = '') { if ($c) { Say ('   PASS  ' + $n + $(if ($x) { '  ' + $x })) } else { $script:fail++; $script:why += $n; Say ('   FAIL  ' + $n + $(if ($x) { '  ' + $x })) } }
function Info($m) { Say ('   INFO  ' + $m) }
function LfSha([string]$p) {
  if (-not (Test-Path -LiteralPath $p)) { return 'MISSING' }
  $enc = [Text.Encoding]::GetEncoding(28591); $x = $enc.GetBytes($enc.GetString([IO.File]::ReadAllBytes($p)).Replace("`r`n", "`n"))
  return ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($x)) -replace '-', '').ToLower()
}
$store = "$App\sm_evidence_store"; $stateRoot = "$App\sm_collector_state"; $live = "$stateRoot\live"; $b = "$App\backend"
$settings = "$App\collector_tools\collector.settings.txt"; $attf = "$App\collector_tools\shared_key_attestation.json"; $wrapper = "$App\collector_tools\run_engine20_collection.ps1"
$killSched = "$Smlive\LIVE_SCHEDULE_KILL"
Say "=== v6 SCHEDULED LIVE run $stamp  (budget: $Authorize per run, $DailyLimit per New York day; F7 options capture: $($WithF7.IsPresent); plan only: $($PlanOnly.IsPresent))"
Jl @{ event = 'STARTED'; authorize = $Authorize; daily_limit = $DailyLimit; plan_only = $PlanOnly.IsPresent }
Say "=== 1. gates (read-only)"
Ok "schedule stop switch absent ($killSched)" (-not (Test-Path -LiteralPath $killSched))
$kit = [ordered]@{ 'smart_move_collection_runner.py' = '250464de825c4d66c973197e6f19a09f3e23559f26c3a86ae5f8e58f217a9c3e'; 'smart_move_monday_bmo.py' = '5a926530b29417ad246b1a3caa069d8ccfea27f783976bb513be0b99ed28ecbe'; 'smart_move_outcomes.py' = '0572074c4d0cf81f010fc9e07a774ee59efc9a48dea07cd813d32f2e89ffe4ed'; 'smart_move_engine20.py' = '467470c36dd83fc0b229c45e86282e9baa00a47f5ee4001515d1790aefec6ee6'; 'smart_move_live.py' = '8c659ab88f05778559bf01339c9a530bba078c3515b0c28d66fc78d9d31f4270'; 'smart_move_calendar_session.py' = '48addc8e831a560d07b6c55ac4248fce062a0a2e99d0eb580683a5a5b5311573'; 'smart_move_calendar_tiers.py' = '05f2edb87404bc61d704d3e2b3a36d36954b0d761cd88272f6eeda55645ee4e9'; 'smart_move_calendar_changes.py' = 'f7b6bda580e62c408e5a3befb96162e7907ec36222832ecf843b9136657dca2a'; 'smart_move_universe.py' = '3ca98cfef84d07a4ec001a29efe93c8c303ce74ebef08bdd431f45213fbd2cd0'; 'smart_move_live_publisher.py' = 'fb6c0b9f307f0d2fecf2ed59c44513f8370f5078fef644b11c49a633772be606'; 'smart_move_calendar_guard.py' = 'b4d2994ef328a7a432d95f3561e65d5e8f225b7429fd7401f1c9e33002980ec0'; 'smart_move_collection_jobs.py' = '81d8d2d4d7bbcca948c6ff0aaf1cdd941680e7996a26bb21d3172bc22a36e411'; 'smart_move_event_records.py' = '543e38dad350cfb141289fbf236fc62b9f38e690ef4e300d6b77fe42364586ea' }
$kitBad = @(); foreach ($n in $kit.Keys) { if ((LfSha "$b\$n") -ne $kit[$n]) { $kitBad += $n } }
Ok "installed engine = exactly kit v6.7 (v6.4 + market-cap floor 7,000,000 USD in the collector; every protected file unchanged)" ($kitBad.Count -eq 0) ($kitBad -join ', ')
Ok "installed collector wrapper = exactly wrapper v3 (96d2396a...; MaxRun 500)" ((LfSha $wrapper) -eq '96d2396a89e4f5daa9d6a8fb2222fdbf8897e5fe959b1a9b2623964691c79e0c')
Ok "frozen scorer pinned (62e09aa0ed785e2a...)" ((LfSha "$App\docs\smart_move\shadow_impl\smvshadow\scorer.py").StartsWith('62e09aa0ed785e2a'))
$st = if (Test-Path -LiteralPath $settings) { Get-Content -LiteralPath $settings -Raw } else { '' }
Ok "settings file Mode is DRY_RUN (unchanged by this script)" ($st -match '(?m)^\s*Mode\s*=\s*DRY_RUN\s*$')
Ok "settings limits are the approved 500 / 700" (($st -match '(?m)^\s*Authorize\s*=\s*500\s*$') -and ($st -match '(?m)^\s*DailyLimit\s*=\s*700\s*$'))
Ok ("budget passed to this run is the approved 500 / 700 (a smaller pilot budget needs -PilotApproved YES-PILOT-" + $Authorize + "-" + $DailyLimit + ")") (($Authorize -eq 500 -and $DailyLimit -eq 700) -or ($PilotApproved -ceq ('YES-PILOT-' + $Authorize + '-' + $DailyLimit)))
$self = Get-ScheduledTask -TaskName $SelfTask -ErrorAction SilentlyContinue
if ($self) { $selfArgs = (($self.Actions | ForEach-Object { $_.Arguments }) -join ' '); Ok "the scheduled task $SelfTask passes -Authorize 500 -DailyLimit 700 -WithF7" (($selfArgs -match '(?i)-Authorize\s+500\b') -and ($selfArgs -match '(?i)-WithF7\b') -and ($selfArgs -match '(?i)-DailyLimit\s+700\b')) } else { Info "task $SelfTask not found (manual run?)" }
Ok "settings enable P1 (CalendarHorizonDays=30, SelectPolicy=deadline_priority)" (($st -match '(?m)^\s*CalendarHorizonDays\s*=\s*30\s*$') -and ($st -match '(?m)^\s*SelectPolicy\s*=\s*deadline_priority\s*$'))
Ok "SMART_MOVE_COLLECTION_LIVE is not already set (process, user, machine)" (-not ($env:SMART_MOVE_COLLECTION_LIVE -or [Environment]::GetEnvironmentVariable('SMART_MOVE_COLLECTION_LIVE', 'User') -or [Environment]::GetEnvironmentVariable('SMART_MOVE_COLLECTION_LIVE', 'Machine')))
Ok "runner kill switch present" (Test-Path -LiteralPath "$store\KILL")
Ok "wrapper kill switch absent" (-not (Test-Path -LiteralPath "$stateRoot\KILL"))
Ok "no collector lock and no collector process" ((-not (Test-Path -LiteralPath "$stateRoot\collection.lock")) -and (@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessId -ne $PID -and $_.Name -match '^(powershell|pwsh|python)' -and $_.CommandLine -match 'run_engine20_collection|smart_move_collection_runner' }).Count -eq 0))
$hoursLeft = $null; $att = $null
try { $att = Get-Content -LiteralPath $attf -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json; $signed = [datetime]::Parse("$($att.attested_at)", [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal).ToUniversalTime(); $hoursLeft = 24 - ((Get-Date).ToUniversalTime() - $signed).TotalHours } catch { }
Ok ("owner attestation valid for at least 0.5 more hour (hours left: " + $(if ($null -ne $hoursLeft) { [math]::Round($hoursLeft, 1) } else { 'unreadable or missing' }) + "; renew it with the one-line command)") ($null -ne $hoursLeft -and $hoursLeft -ge 0.5)
if ($att) { Ok "attestation names production as the designated collector, allows market hours, states the production cap of 2" (($att.designated_collector -eq 'production') -and ($att.market_hours_ok -eq $true) -and ($att.production_uw_max_inflight -eq 2) -and ($att.acknowledges_shared_token_three_slot_limit -eq $true) -and ($att.acknowledges_production_and_other_app_use_not_visible -eq $true)) }
$used = $null; try { $used = (Get-Content -LiteralPath "$live\live_status.json" -Raw -ErrorAction Stop | ConvertFrom-Json).requests.used_today } catch { }
Info ("requests already used today per the status file: " + $(if ($null -ne $used) { $used } else { 'unknown' }) + " (the governor enforces $DailyLimit per New York day)")
$t = Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
if ($t) { $ti = Get-ScheduledTaskInfo -TaskName $Task; $mins = ($ti.NextRunTime - (Get-Date)).TotalMinutes
  Ok "hourly task does not pass -Live" (((($t.Actions | ForEach-Object { $_.Arguments }) -join ' ') -notmatch '(?i)-Live'))
  Ok ("next hourly DRY_RUN trigger is " + [math]::Round($mins, 1) + " min away (need at least 10)") ($mins -ge 10) } else { Ok "hourly task exists" $false }
if ($script:fail -gt 0) {
  Say ''; Say ("REFUSED: gates failed (" + $script:fail + "): " + ($script:why -join ' | '))
  Jl @{ event = 'SKIPPED'; failed = $script:fail; reasons = ($script:why -join ' | ') }
  # v6.3: a refusal is no longer invisible. The dashboard reads collection_runs.jsonl (event BLOCKED => state BLOCKED) and
  # last_gate_refusal.json; Task Scheduler sees exit 3 instead of 0 (the 2026-11-10 incident ran 18 h on 'success').
  try { New-Item -ItemType Directory -Force -Path $live | Out-Null
        $rec = @{ event = 'BLOCKED'; at = (Get-Date).ToUniversalTime().ToString('o'); mode = 'LIVE'; source = 'v6_7_live_scheduled.ps1'; reason = ('gates failed: ' + ($script:why -join ' | ')) }
        Add-Content -LiteralPath "$live\collection_runs.jsonl" -Value ($rec | ConvertTo-Json -Compress) -Encoding ascii
        [IO.File]::WriteAllText("$live\last_gate_refusal.json", ($rec | ConvertTo-Json)) } catch { Say ('   WARNING: could not write the refusal record: ' + $_.Exception.Message) }
  Stop-Transcript | Out-Null; exit 3 }
if ($PlanOnly) { Say ''; Say 'All gates PASS. PLAN ONLY (nothing started).'; Jl @{ event = 'PLAN_ONLY_OK' }; Stop-Transcript | Out-Null; exit 5 }

$code = 99
try {
  Say "=== 2. LIVE run (process-only live flag)"
  $env:SMART_MOVE_COLLECTION_LIVE = '1'
  $wargs = @('-SettingsFile', $settings, '-Live', '-Attestation', $attf, '-Authorize', $Authorize, '-DailyLimit', $DailyLimit); if ($WithF7) { $wargs += '-WithF7' }
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $wrapper @wargs 2>&1 | ForEach-Object { Say ('   ' + [string]$_) }
  $code = $LASTEXITCODE
}
finally {
  Remove-Item Env:SMART_MOVE_COLLECTION_LIVE -ErrorAction SilentlyContinue
  Say "=== 3. close-out"
  Say ('   process live flag removed: ' + (-not $env:SMART_MOVE_COLLECTION_LIVE))
  if (-not (Test-Path -LiteralPath "$store\KILL")) { [IO.File]::WriteAllText("$store\KILL", "re-engaged by v6_7_live_scheduled.ps1 after the run (the runner did not leave it engaged)"); Say '   WARNING: the runner kill switch was missing after the run; it has been re-created.' } else { Say '   runner kill switch present again: True' }
  Say ('   wrapper kill switch absent: ' + (-not (Test-Path -LiteralPath "$stateRoot\KILL")) + ' | collector lock released: ' + (-not (Test-Path -LiteralPath "$stateRoot\collection.lock")))
  Say ('   settings file Mode still DRY_RUN: ' + ((Get-Content -LiteralPath $settings | Where-Object { $_ -match '^\s*Mode\s*=\s*DRY_RUN\s*$' }).Count -eq 1))
  $att2 = 0; try { $last = Get-Content -LiteralPath "$live\collection_runs.jsonl" -Tail 1 -ErrorAction Stop | ConvertFrom-Json; if ($last.event -eq 'FINISHED') { $att2 = $last.attempts } } catch { }
  Jl @{ event = 'FINISHED'; exit_code = $code; attempts = $att2 }
}
$lastEv = $null; try { $lastEv = (Get-Content -LiteralPath "$live\collection_runs.jsonl" -Tail 1 -ErrorAction Stop | ConvertFrom-Json).event } catch { }
if ($code -eq 0 -and $lastEv -ne 'FINISHED') { $code = 9 }
Say ("=== RUN FINISHED with exit code $code  (0 = completed; 2 = wrapper blocked; 3 = gates refused; 5 = plan only; 6 = live gate; 7 = attestation; 8 = ledger integrity; 9 = wrapper skipped without a run)")
Say "Transcript: $report"
Stop-Transcript | Out-Null
exit $code
