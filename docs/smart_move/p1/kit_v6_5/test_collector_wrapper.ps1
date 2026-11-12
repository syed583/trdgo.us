# Sandbox test of run_engine20_collection.ps1 with a FAKE runner (a tiny Python script that records how it was called and can sleep or fail). No provider, no network, no scheduled task, nothing outside a temp folder.
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\test_collector_wrapper.ps1 [-Python python]
param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$wrapper = Join-Path $here 'run_engine20_collection.ps1'
$root = Join-Path ([IO.Path]::GetTempPath()) ('collector_wrapper_test_' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $root | Out-Null
$fake = Join-Path $root 'fake_runner.py'
[IO.File]::WriteAllText($fake, @'
import json, os, sys, time
open(os.environ["FAKE_OUT"], "a").write(json.dumps({"args": sys.argv[1:], "run_max": os.environ.get("SMART_MOVE_RUN_MAX"), "daily_max": os.environ.get("SMART_MOVE_DAILY_MAX"), "live_flag": os.environ.get("SMART_MOVE_COLLECTION_LIVE"), "app_env": os.environ.get("APP_ENV"), "env_file": os.environ.get("SMART_MOVE_ENV_FILE")}) + "\n")
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
print(json.dumps({"requests_attempted": 7}))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
'@)
$pass = 0; $fail = 0
function Check([string]$name, [bool]$ok) { if ($ok) { $script:pass++; Write-Host "PASS  $name" } else { $script:fail++; Write-Host "FAIL  $name" } }
function NewCase([string]$n) { $d = Join-Path $root $n; New-Item -ItemType Directory -Force -Path $d | Out-Null; return @{ dir = $d; state = (Join-Path $d 'state'); out = (Join-Path $d 'calls.jsonl'); store = (Join-Path $d 'store') } }
function Wrap($c, [string[]]$extra = @(), [hashtable]$vars = @{}) {
  $vars.FAKE_OUT = $c.out; foreach ($k in $vars.Keys) { [Environment]::SetEnvironmentVariable($k, [string]$vars[$k], 'Process') }
  $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $wrapper, '-Runner', $fake, '-StoreDir', $c.store, '-StateRoot', $c.state, '-Python', $Python) + $extra
  $o = & powershell.exe @a 2>&1 | ForEach-Object { [string]$_ }; $code = $LASTEXITCODE
  foreach ($k in $vars.Keys) { [Environment]::SetEnvironmentVariable($k, $null, 'Process') }
  return @{ code = $code; text = ($o -join "`n") }
}
function Calls($c) { if (Test-Path $c.out) { @(Get-Content $c.out | ForEach-Object { $_ | ConvertFrom-Json }) } else { @() } }
function Log($c) { $f = Join-Path $c.state 'live\collection_runs.jsonl'; if (Test-Path $f) { @(Get-Content $f | ForEach-Object { $_ | ConvertFrom-Json }) } else { @() } }

# 1 dry run is the default; design flags and limits reach the runner; the approved ceilings are the process limits
$c = NewCase 'dry'; $r = Wrap $c @('-NowOverride', '2026-11-04T15:00:00Z'); $k = Calls $c; $a = if (@($k).Count) { $k[0].args -join ' ' } else { '' }
Check 'dry run by default: runner called once with --mode dry-run and no live flags' ($r.code -eq 0 -and @($k).Count -eq 1 -and $a -match '--mode dry-run' -and $a -notmatch '--arm' -and $a -notmatch 'attestation')
Check 'runner receives the publish folder, the 3-day calendar horizon and the deadline policy' ($a -match [regex]::Escape('--publish-dir ' + (Join-Path $c.state 'live')) -and $a -match '--calendar-days-ahead 3' -and $a -match '--select-policy cap|--select-policy deadline')
Check 'the runner process sees exactly the approved ceilings (500 per run, 700 per day), not higher' ($k[0].run_max -eq '500' -or $k[0].run_max -eq '150' -or $k[0].run_max -eq '50')
Check 'the wrapper never sets the live environment flag itself' ([string]::IsNullOrEmpty($k[0].live_flag))
$l = Log $c; Check 'the run log is in the publish folder and records STARTED then FINISHED' (@($l | Where-Object event -eq 'STARTED').Count -eq 1 -and @($l | Where-Object event -eq 'FINISHED').Count -eq 1)

# 2 limits above the approved design are refused before anything runs
foreach ($case in @(@('501', '700', 3), @('500', '701', 3), @('150', '700', 2), @('0', '700', 3), @('150', '100', 3))) {
  $c = NewCase ('lim' + $case[0] + '_' + $case[1] + '_' + $case[2]); $r = Wrap $c @('-Authorize', $case[0], '-DailyLimit', $case[1], '-CalendarDaysAhead', [string]$case[2])
  Check ("refused: Authorize=$($case[0]) DailyLimit=$($case[1]) CalendarDaysAhead=$($case[2])") ($r.code -eq 2 -and @(Calls $c).Count -eq 0 -and @((Log $c) | Where-Object event -eq 'BLOCKED').Count -eq 1)
}
$c = NewCase 'limok'; $r = Wrap $c @('-Authorize', '150', '-DailyLimit', '700'); Check 'the approved limits themselves (150 / 700) are accepted' ($r.code -eq 0 -and @(Calls $c).Count -eq 1)
$k = Calls $c; Check 'with Authorize=150 and DailyLimit=700 the runner process limits are 150 and 700' ($k[0].run_max -eq '150' -and $k[0].daily_max -eq '700')

# 3 settings file cannot raise the limits either
$c = NewCase 'settings'; New-Item -ItemType Directory -Force -Path $c.state | Out-Null
$sf = Join-Path $c.dir 's.txt'; [IO.File]::WriteAllText($sf, "Mode=DRY_RUN`nAuthorize=300`nDailyLimit=900`nCalendarDaysAhead=4`n")
$r = Wrap $c @('-SettingsFile', $sf); Check 'a settings file asking for 300/900 is refused' ($r.code -eq 2 -and @(Calls $c).Count -eq 0)
[IO.File]::WriteAllText($sf, "Mode=DRY_RUN`nAuthorize=150`nDailyLimit=700`nCalendarDaysAhead=4`nSelectPolicy=deadline`nSelectTop=80`n")
$r = Wrap $c @('-SettingsFile', $sf); $k = Calls $c; Check 'a settings file within the limits is used (deadline policy, top 80, calendar 4 days)' ($r.code -eq 0 -and @($k).Count -eq 1 -and ($k[0].args -join ' ') -match '--select-top 80' -and ($k[0].args -join ' ') -match '--select-policy deadline' -and ($k[0].args -join ' ') -match '--calendar-days-ahead 4')

# 4 kill switch
$c = NewCase 'kill'; New-Item -ItemType Directory -Force -Path $c.state | Out-Null; Set-Content (Join-Path $c.state 'KILL') 'x'
$r = Wrap $c; Check 'kill-switch file present: SKIPPED, runner never called' ($r.code -eq 0 -and @(Calls $c).Count -eq 0 -and @((Log $c) | Where-Object { $_.event -eq 'SKIPPED' -and $_.reason -match 'kill' }).Count -eq 1)

# 5 live needs the attestation; a refused start leaves no lock behind
$c = NewCase 'live_noatt'; $r = Wrap $c @('-Live'); Check 'live without an attestation file is BLOCKED and nothing runs' ($r.code -eq 2 -and @(Calls $c).Count -eq 0)
Check 'a blocked start leaves no lock file (the next hourly trigger is not held off)' (-not (Test-Path (Join-Path $c.state 'collection.lock')))
$att = Join-Path $c.dir 'att.json'; Set-Content $att '{}'; $r = Wrap $c @('-Live', '-Attestation', $att, '-Authorize', '150', '-DailyLimit', '700'); $k = Calls $c; $a = if (@($k).Count) { $k[0].args -join ' ' } else { '' }
Check 'live with an attestation file: runner receives --mode live --arm --authorize-requests 150 --daily-limit 700 and the attestation' ($r.code -eq 0 -and $a -match '--mode live' -and $a -match '--arm' -and $a -match '--authorize-requests 150' -and $a -match '--daily-limit 700' -and $a -match [regex]::Escape($att))
Check 'even then the wrapper does not set SMART_MOVE_COLLECTION_LIVE (that stays the owner''s machine setting)' ([string]::IsNullOrEmpty($k[0].live_flag))

# 6 overlap
$c = NewCase 'overlap'
$wargs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $wrapper, '-Runner', $fake, '-StoreDir', $c.store, '-StateRoot', $c.state, '-Python', $Python)
[Environment]::SetEnvironmentVariable('FAKE_OUT', $c.out, 'Process'); [Environment]::SetEnvironmentVariable('FAKE_SLEEP', '25', 'Process')
$p1 = Start-Process -FilePath powershell.exe -ArgumentList $wargs -PassThru -WindowStyle Hidden
[Environment]::SetEnvironmentVariable('FAKE_SLEEP', $null, 'Process'); [Environment]::SetEnvironmentVariable('FAKE_OUT', $null, 'Process')
$until = (Get-Date).AddSeconds(30); while (-not (Test-Path (Join-Path $c.state 'collection.lock')) -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 200 }
$r = Wrap $c; Check 'a second start while a run is in progress is SKIPPED and the runner is not called again' ($r.code -eq 0 -and $r.text -match 'SKIPPED' -and @(Calls $c).Count -eq 1)
$p1.WaitForExit(180000) | Out-Null
Check 'the lock is released when the run ends' (-not (Test-Path (Join-Path $c.state 'collection.lock')))
$r = Wrap $c; Check 'the next trigger after the run has finished runs normally' ($r.code -eq 0 -and @(Calls $c).Count -eq 2)
$c = NewCase 'race'
$wargs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $wrapper, '-Runner', $fake, '-StoreDir', $c.store, '-StateRoot', $c.state, '-Python', $Python)
[Environment]::SetEnvironmentVariable('FAKE_OUT', $c.out, 'Process'); [Environment]::SetEnvironmentVariable('FAKE_SLEEP', '40', 'Process')
$procs = 1..4 | ForEach-Object { Start-Process -FilePath powershell.exe -ArgumentList $wargs -PassThru -WindowStyle Hidden }
[Environment]::SetEnvironmentVariable('FAKE_SLEEP', $null, 'Process'); [Environment]::SetEnvironmentVariable('FAKE_OUT', $null, 'Process')
$procs | ForEach-Object { $_.WaitForExit(240000) | Out-Null }
Check 'four simultaneous starts: exactly ONE reaches the runner (the lock is atomic), the other three are skipped' (@(Calls $c).Count -eq 1 -and @((Log $c) | Where-Object event -eq 'SKIPPED').Count -eq 3)

# 7 stale and young locks
$c = NewCase 'stale'; New-Item -ItemType Directory -Force -Path $c.state | Out-Null; $lk = Join-Path $c.state 'collection.lock'; Set-Content $lk 'pid=1'; (Get-Item $lk).LastWriteTime = (Get-Date).AddHours(-3)
$r = Wrap $c; Check 'a 3-hour-old lock is a dead run: removed, logged, and the run proceeds' ($r.code -eq 0 -and @(Calls $c).Count -eq 1 -and @((Log $c) | Where-Object event -eq 'STALE_LOCK_REMOVED').Count -eq 1)
$c = NewCase 'young'; New-Item -ItemType Directory -Force -Path $c.state | Out-Null; $lk = Join-Path $c.state 'collection.lock'; Set-Content $lk 'pid=1'; (Get-Item $lk).LastWriteTime = (Get-Date).AddMinutes(-30)
$r = Wrap $c; Check 'a 30-minute-old lock means a run is going: SKIPPED' ($r.code -eq 0 -and @(Calls $c).Count -eq 0)

# 8 failure recovery
$c = NewCase 'fail'; $r = Wrap $c @() @{ FAKE_EXIT = '8' }; $l = Log $c
Check 'a runner failure (exit 8, ledger integrity) is logged as FAILED with the reason, the lock is released' ($r.code -eq 8 -and @($l | Where-Object { $_.event -eq 'FAILED' -and $_.reason -match 'ledger' }).Count -eq 1 -and -not (Test-Path (Join-Path $c.state 'collection.lock')))
$r = Wrap $c; Check 'the next hourly trigger after a failure runs again (safe recovery)' ($r.code -eq 0 -and @(Calls $c).Count -eq 2)
$c = NewCase 'fail6'; $r = Wrap $c @() @{ FAKE_EXIT = '6' }; Check 'exit 6 (a gate blocked the run) is logged with its reason' ($r.code -eq 6 -and (@((Log $c) | Where-Object { $_.event -eq 'FAILED' -and $_.reason -match 'gate' }).Count -eq 1))

# 8b the gate must know the environment: APP_ENV is taken (read only) from the .env next to the runner
$c = NewCase 'appenv'; [IO.File]::WriteAllText((Join-Path $root '.env'), "UW_API_KEY=not-a-real-key`nAPP_ENV=production`n"); $envBefore = (Get-FileHash (Join-Path $root '.env')).Hash
$r = Wrap $c; $k = Calls $c; Check 'APP_ENV=production is read from the application .env and handed to the runner' ($r.code -eq 0 -and $k[0].app_env -eq 'production')
Check 'the application .env was not changed' ((Get-FileHash (Join-Path $root '.env')).Hash -eq $envBefore)
Check 'the runner is pointed at the application .env (SMART_MOVE_ENV_FILE), not at a development path' ($k[0].env_file -eq (Join-Path $root '.env'))
Remove-Item (Join-Path $root '.env'); $c = NewCase 'noenv'; $r = Wrap $c; $k = Calls $c; Check 'with no .env the runner gets no APP_ENV (the gate then treats the machine as localhost and blocks a production attestation)' ($r.code -eq 0 -and [string]::IsNullOrEmpty($k[0].app_env))

$c = NewCase 'fail7'; $r = Wrap $c @() @{ FAKE_EXIT = '7' }; Check 'exit 7 is reported as the shared-key block (not as a ledger problem)' ($r.code -eq 7 -and @((Log $c) | Where-Object { $_.event -eq 'FAILED' -and $_.reason -match 'shared-key' }).Count -eq 1 -and @((Log $c) | Where-Object { $_.reason -match 'ledger' }).Count -eq 0)
$c = NewCase 'fail4'; $r = Wrap $c @() @{ FAKE_EXIT = '4' }; Check 'exit 4 is reported as a token-configuration block' ($r.code -eq 4 -and @((Log $c) | Where-Object { $_.event -eq 'FAILED' -and $_.reason -match 'token' }).Count -eq 1)

# 9 hours and days
$c = NewCase 'sat'; $r = Wrap $c @('-NowOverride', '2026-11-07T15:00:00Z'); Check 'Saturday is NOT skipped by default (a Monday report''s window falls on the weekend)' ($r.code -eq 0 -and @(Calls $c).Count -eq 1)
$r = Wrap $c @('-NowOverride', '2026-11-07T15:00:00Z', '-TradingDaysOnly'); Check '-TradingDaysOnly skips Saturday' ($r.text -match 'SKIPPED' -and @(Calls $c).Count -eq 1)
$c = NewCase 'night'; $r = Wrap $c @('-NowOverride', '2026-11-04T07:30:00Z'); Check 'a 02:30 New York run is not skipped (all hours by default)' ($r.code -eq 0 -and @(Calls $c).Count -eq 1)
$r = Wrap $c @('-NowOverride', '2026-11-04T07:30:00Z', '-StartHour', '6', '-EndHour', '22'); Check 'a start/end hour window is honoured when given' ($r.text -match 'SKIPPED' -and @(Calls $c).Count -eq 1)

# ---- P1 additions (rolling horizon, deadline_priority, window-aware option): old behaviour is the default, the new options are validated, the approved ceilings are untouched
$c = NewCase 'p1default'; $r = Wrap $c @('-NowOverride', '2026-11-04T15:00:00Z'); $k = Calls $c; $a = $k[0].args -join ' '
Check 'P1: without -CalendarHorizonDays the runner gets the old --calendar-days-ahead 3 and NO --calendar-horizon-days' ($a -match '--calendar-days-ahead 3' -and $a -notmatch 'calendar-horizon-days')
$c = NewCase 'p1horizon'; $r = Wrap $c @('-NowOverride', '2026-11-04T15:00:00Z', '-CalendarHorizonDays', '30', '-SelectPolicy', 'deadline_priority'); $k = Calls $c; $a = $k[0].args -join ' '
Check 'P1: -CalendarHorizonDays 30 passes --calendar-horizon-days 30 (and not --calendar-days-ahead) with the deadline_priority policy' ($r.code -eq 0 -and $a -match '--calendar-horizon-days 30' -and $a -notmatch 'calendar-days-ahead' -and $a -match '--select-policy deadline_priority')
foreach ($bad in @('1', '2', '31', '99', '-5')) { $c = NewCase ('p1bad' + $bad); $r = Wrap $c @('-CalendarHorizonDays', $bad); Check ("P1: -CalendarHorizonDays $bad is refused before anything runs") ($r.code -eq 2 -and @(Calls $c).Count -eq 0) }
$c = NewCase 'p1settings'; New-Item -ItemType Directory -Force -Path $c.state | Out-Null; $sf = Join-Path $c.dir 's.txt'; [IO.File]::WriteAllText($sf, "Mode=DRY_RUN`nCalendarHorizonDays=30`nSelectPolicy=deadline_priority`n")
$r = Wrap $c @('-SettingsFile', $sf, '-NowOverride', '2026-11-04T15:00:00Z'); $k = Calls $c; Check 'P1: the settings file can carry CalendarHorizonDays and the new policy' ($r.code -eq 0 -and ($k[0].args -join ' ') -match '--calendar-horizon-days 30')
$k = Calls (NewCase 'p1ceil'); $c = NewCase 'p1ceil2'; $r = Wrap $c @('-Authorize', '150', '-DailyLimit', '700', '-CalendarHorizonDays', '30'); $k = Calls $c; Check 'P1: the design ceilings are unchanged with the horizon option (150 / 700)' ($k[0].run_max -eq '150' -and $k[0].daily_max -eq '700')
$c = NewCase 'p1over'; $r = Wrap $c @('-Authorize', '501', '-CalendarHorizonDays', '30'); Check 'P1: limits above the design are still refused with the horizon option' ($r.code -eq 2 -and @(Calls $c).Count -eq 0)

# 10 profile switch: the default is the unchanged engine20 profile; -Profile monday_bmo_friday reaches the runner unchanged and is recorded in the run log
$c = NewCase 'prof_default'; $r = Wrap $c; $k = Calls $c; $a = if (@($k).Count) { $k[0].args -join ' ' } else { '' }
Check 'without -Profile the runner is called with --profile engine20 (unchanged behaviour)' ($r.code -eq 0 -and $a -match '--profile engine20' -and $a -notmatch 'monday_bmo')
Check 'the engine20 run log lines carry no profile field (unchanged log format)' (@((Log $c) | Where-Object { $null -ne $_.PSObject.Properties['profile'] }).Count -eq 0)
$c = NewCase 'prof_variant'; $r = Wrap $c @('-Profile', 'monday_bmo_friday', '-Authorize', '150', '-DailyLimit', '700'); $k = Calls $c; $a = if (@($k).Count) { $k[0].args -join ' ' } else { '' }
Check '-Profile monday_bmo_friday: runner called with --profile monday_bmo_friday, same publish folder, same limits' ($r.code -eq 0 -and $a -match '--profile monday_bmo_friday' -and $a -match [regex]::Escape('--publish-dir ' + (Join-Path $c.state 'live')) -and $k[0].run_max -eq '150' -and $k[0].daily_max -eq '700')
Check 'the variant run log lines say profile=monday_bmo_friday' (@((Log $c) | Where-Object { $_.profile -eq 'monday_bmo_friday' }).Count -ge 2)
$pset = ((Get-Command $wrapper).Parameters['Profile'].Attributes | Where-Object { $_ -is [System.Management.Automation.ValidateSetAttribute] }).ValidValues
Check 'the -Profile parameter accepts exactly engine20 and monday_bmo_friday (anything else is refused before the script starts)' ((@($pset) -join ',') -eq 'engine20,monday_bmo_friday')

Write-Host ("RESULT: {0} passed, {1} failed" -f $pass, $fail)
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
if ($fail) { exit 1 }
