<#
Scheduled wrapper around the EXISTING Engine20 collection runner. It adds only what a schedule needs; it never collects anything itself and never changes the frozen engine or the governor:

  * runs every day (add -TradingDaysOnly to skip weekends and NYSE holidays: NOT recommended, a Monday report's window falls on the weekend), within -StartHour..-EndHour New York time (default: every hour, 00:00-23:59, because before-the-open prediction windows close at 06:00 and their fresh-close evidence exists only overnight);
  * refuses to start if the kill-switch file exists, or if a previous run is still going (a lock file; a lock older than 2 hours is treated as a dead run and removed);
  * DRY RUN by default: the runner is called with --mode dry-run, which plans and sends nothing. A live run needs -Live AND an attestation file you signed; the runner itself still enforces its own attestation, token and
    governor checks and will block if any fails;
  * appends one JSON line per trigger (started, skipped-and-why, finished, exit code) to a run log, so a missed window can be explained afterwards.

The runner collects, one request at a time, only what the frozen engine's own plan says is missing for events whose prediction window is open (profile engine20), under the governor limits you pass (-Authorize per run,
-DailyLimit per New York day). Those are YOUR internal limits, not the provider's: the provider allows three simultaneous REST requests and nothing else, and this runner uses one.

  .\run_engine20_collection.ps1 -Runner <path>\smart_move_collection_runner.py -StoreDir <private store> -StateRoot <private state folder>            (dry run)
  .\run_engine20_collection.ps1 ... -Live -Attestation <signed file> -Authorize 50 -DailyLimit 100                                                     (live; separate approval)
#>
[CmdletBinding()]
param(
  [string]$SettingsFile = '',
  [string]$Runner = '',
  [string]$StoreDir = '',
  [string]$StateRoot = '',
  [string]$Python = 'python',
  [string]$Attestation = '',
  [string]$KillFile = '',
  [int]$SelectTop = 20,
  [ValidateSet('cap', 'deadline', 'deadline_priority')][string]$SelectPolicy = 'cap',
  [switch]$TradingDaysOnly,
  [int]$StartHour = 0,
  [int]$EndHour = 23,
  [int]$Authorize = 50,
  [int]$DailyLimit = 100,
  [switch]$WithF7,
  [int]$CalendarDaysAhead = 3,
  [int]$CalendarHorizonDays = 0,
  [string]$PublishDir = '',
  [switch]$Live,
  [string]$NowOverride = '',
  [string]$LogFile = '',
  [ValidateSet('engine20', 'monday_bmo_friday')][string]$Profile = 'engine20'          # monday_bmo_friday = the SEPARATE Friday-window variant for Monday-BMO events (own ledger folder under PublishDir; frozen model untouched). Default unchanged.
)
$ErrorActionPreference = 'Stop'
if ($SettingsFile) {
  # KEY=VALUE lines (see collector.settings.template.txt), read on every run. Mode defaults to DRY RUN; only the word LIVE makes a run live.
  if (-not (Test-Path -LiteralPath $SettingsFile)) { Write-Host "BLOCKED: settings file not found: $SettingsFile"; exit 2 }
  $kv = @{}; foreach ($line in Get-Content -LiteralPath $SettingsFile) { if ($line -match '^\s*([A-Za-z]+)\s*=\s*(.*?)\s*$' -and -not $line.TrimStart().StartsWith('#')) { $kv[$Matches[1]] = $Matches[2] } }
  foreach ($k in 'Runner', 'StoreDir', 'StateRoot', 'Python', 'SelectPolicy', 'Attestation', 'PublishDir') { if ($kv.ContainsKey($k) -and $kv[$k] -and -not $PSBoundParameters.ContainsKey($k)) { Set-Variable -Name $k -Value $kv[$k] } }
  foreach ($k in 'SelectTop', 'Authorize', 'DailyLimit', 'CalendarDaysAhead', 'CalendarHorizonDays') { if ($kv.ContainsKey($k) -and $kv[$k] -and -not $PSBoundParameters.ContainsKey($k)) { Set-Variable -Name $k -Value ([int]$kv[$k]) } }
  if ($kv['Mode'] -eq 'LIVE' -and -not $PSBoundParameters.ContainsKey('Live')) { $Live = [switch]$true }
}
if (-not $Runner -or -not $StoreDir -or -not $StateRoot) { Write-Host 'BLOCKED: Runner, StoreDir and StateRoot are required (parameters or settings file)'; exit 2 }
New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
if (-not $PublishDir) { $PublishDir = Join-Path $StateRoot 'live' }
New-Item -ItemType Directory -Force -Path $PublishDir | Out-Null                       # the folder the dashboard reads: prediction ledger, status file and this wrapper's own run log
if (-not $LogFile) { $LogFile = Join-Path $PublishDir 'collection_runs.jsonl' }
if (-not $KillFile) { $KillFile = Join-Path $StateRoot 'KILL' }
$nowUtc = if ($NowOverride) { [DateTime]::Parse($NowOverride).ToUniversalTime() } else { [DateTime]::UtcNow }
try { $et = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId($nowUtc, 'Eastern Standard Time') } catch { $et = $nowUtc.AddHours(-5) }
function Log([hashtable]$o) { $o.at = (Get-Date).ToUniversalTime().ToString('o'); $o.mode = $(if ($Live) { 'LIVE' } else { 'DRY_RUN' }); $o.et = $et.ToString('yyyy-MM-dd HH:mm'); if ($Profile -ne 'engine20') { $o.profile = $Profile }; Add-Content -LiteralPath $LogFile -Value ($o | ConvertTo-Json -Compress) -Encoding ascii }
function Skip([string]$why) { Log @{ event = 'SKIPPED'; reason = $why }; Write-Host "SKIPPED: $why"; exit 0 }
# The design limits the owner approved (700 provider requests per New York day, 150 per run, one request at a time). A settings file cannot raise them; changing them means editing this script, i.e. a new approval.
$MaxRun = 150; $MaxDaily = 700
if ($Authorize -lt 1 -or $Authorize -gt $MaxRun -or $DailyLimit -lt $Authorize -or $DailyLimit -gt $MaxDaily) { Log @{ event = 'BLOCKED'; reason = ('limits outside the approved design (per run 1..{0}, per day up to {1}): Authorize={2} DailyLimit={3}' -f $MaxRun, $MaxDaily, $Authorize, $DailyLimit) }; Write-Host 'BLOCKED: Authorize/DailyLimit are outside the approved limits'; exit 2 }
if ($CalendarHorizonDays -ne 0 -and ($CalendarHorizonDays -lt 3 -or $CalendarHorizonDays -gt 30)) { Log @{ event = 'BLOCKED'; reason = "CalendarHorizonDays must be 0 (off) or 3..30, was $CalendarHorizonDays" }; Write-Host 'BLOCKED: CalendarHorizonDays must be 0 or between 3 and 30'; exit 2 }
if ($CalendarDaysAhead -lt 3 -or $CalendarDaysAhead -gt 7) { Log @{ event = 'BLOCKED'; reason = "CalendarDaysAhead must be 3..7, was $CalendarDaysAhead" }; Write-Host 'BLOCKED: CalendarDaysAhead must be between 3 and 7'; exit 2 }

# NYSE full-day closures (the exchange's published schedule; early closes still trade)
$holidays = @('2026-11-26', '2026-12-25', '2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31', '2027-06-18', '2027-07-05', '2027-09-06', '2027-11-25', '2027-12-24')
# Prediction windows are measured in clock hours (24 h long, ending 24 h before the report), so a Monday report has its window on Saturday and Sunday: weekends and holidays are NOT skipped by default.
if ($TradingDaysOnly -and ($et.DayOfWeek -eq 'Saturday' -or $et.DayOfWeek -eq 'Sunday')) { Skip 'weekend (-TradingDaysOnly)' }
if ($TradingDaysOnly -and ($holidays -contains $et.ToString('yyyy-MM-dd'))) { Skip 'NYSE holiday (-TradingDaysOnly)' }
if ($et.Hour -lt $StartHour -or $et.Hour -gt $EndHour) { Skip ('outside {0:00}:00-{1:00}:59 New York time' -f $StartHour, $EndHour) }
if (Test-Path -LiteralPath $KillFile) { Skip 'kill-switch file present' }

$lock = Join-Path $StateRoot 'collection.lock'
if (Test-Path -LiteralPath $lock) {
  if (((Get-Date) - (Get-Item -LiteralPath $lock).LastWriteTime).TotalHours -lt 2) { Skip 'a previous run is still in progress (lock younger than 2 hours)' }
  try { Remove-Item -LiteralPath $lock -Force; Log @{ event = 'STALE_LOCK_REMOVED' } } catch { Skip 'a previous run still holds the lock' }
}
function Take-Lock {
  try { return [IO.File]::Open($lock, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None) } catch [IO.IOException] { return $null }
}
$lockStream = $null
function Release-Lock { if ($script:lockStream) { $script:lockStream.Dispose() }; Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue }
if ($Live -and (-not $Attestation -or -not (Test-Path -LiteralPath $Attestation))) { Log @{ event = 'BLOCKED'; reason = 'live run without an attestation file' }; Write-Host 'BLOCKED: a live run needs -Attestation <the file you signed>'; exit 2 }
if (-not (Test-Path -LiteralPath $Runner)) { Log @{ event = 'BLOCKED'; reason = 'runner not found' }; Write-Host "BLOCKED: runner not found: $Runner"; exit 2 }

$lockStream = Take-Lock
if (-not $lockStream) { Skip 'another run took the lock at the same moment' }
$run = Join-Path $StateRoot ('run_' + $nowUtc.ToString('yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path $run | Out-Null
$args2 = @($Runner, '--profile', $Profile, '--store-dir', $StoreDir, '--state-dir', $run, '--select-top', $SelectTop, '--select-policy', $SelectPolicy, '--publish-dir', $PublishDir)
if ($CalendarHorizonDays -gt 0) { $args2 += @('--calendar-horizon-days', $CalendarHorizonDays) } else { $args2 += @('--calendar-days-ahead', $CalendarDaysAhead) }     # P1: tiered rolling horizon replaces the fixed-day refresh when set
if ($WithF7) { $args2 += '--with-f7' }
if ($Live) { $args2 += @('--mode', 'live', '--arm', '--authorize-requests', $Authorize, '--daily-limit', $DailyLimit, '--shared-key-attestation', $Attestation) } else { $args2 += @('--mode', 'dry-run') }
if (-not $env:SMART_MOVE_ENV_FILE) {      # the runner must read the application's own .env (token configuration check), not the path of the development PC
  $appEnv0 = Join-Path (Split-Path -Parent $Runner) '.env'
  if (Test-Path -LiteralPath $appEnv0) { $env:SMART_MOVE_ENV_FILE = $appEnv0 }
}
if (-not $env:APP_ENV) {      # the shared-key gate must know it runs on the production server: take APP_ENV from the application's own .env next to the runner (read only; the file is never changed)
  $appEnv = Join-Path (Split-Path -Parent $Runner) '.env'
  if (Test-Path -LiteralPath $appEnv) { $m = Select-String -LiteralPath $appEnv -Pattern '^\s*APP_ENV\s*=\s*(.+?)\s*$' | Select-Object -First 1; if ($m) { $env:APP_ENV = $m.Matches[0].Groups[1].Value.Trim([char]34).Trim([char]39) } }
}
$env:SMART_MOVE_RUN_MAX = [string]$Authorize; $env:SMART_MOVE_DAILY_MAX = [string]$DailyLimit        # this process only: the runner's own ceilings become exactly the approved numbers, no higher
try {
  $pidBytes = [Text.Encoding]::ASCII.GetBytes("pid=$PID"); $lockStream.Write($pidBytes, 0, $pidBytes.Length); $lockStream.Flush()
  Log @{ event = 'STARTED'; state_dir = $run; daily_limit = $(if ($Live) { $DailyLimit } else { $null }) }
  $code = 0
  & $Python @args2 | Out-File -FilePath (Join-Path $run 'console.txt') -Encoding ascii
  $code = $LASTEXITCODE
  $att = 0; $txt = Get-Content -LiteralPath (Join-Path $run 'console.txt') -Raw -ErrorAction SilentlyContinue
  if ($txt -match '"requests_attempted":\s*(\d+)') { $att = [int]$Matches[1] }
  Log @{ event = 'FINISHED'; exit_code = $code; state_dir = $run; attempts = $att }
  if ($code -ne 0) { Log @{ event = 'FAILED'; exit_code = $code; reason = $(switch ($code) { 4 { 'blocked: token configuration (credential missing or malformed)' } 6 { 'blocked by a live gate (machine flag, authorisation, kill switch, limits, calendar)' } 7 { 'blocked: shared-key safety not verified (no valid owner attestation)' } 8 { 'prediction ledger failed its integrity check' } default { 'runner error' } }) } }
  Write-Host ("FINISHED exit code {0}; state in {1}" -f $code, $run)
  exit $code
} catch {
  Log @{ event = 'FAILED'; exit_code = 1; reason = $_.Exception.Message }
  Write-Host ("FAILED: " + $_.Exception.Message)
  exit 1
} finally {
  Release-Lock
}
