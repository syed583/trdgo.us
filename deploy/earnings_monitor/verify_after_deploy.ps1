<#
  READ-ONLY post-deployment verification of the Earnings Monitor. It only calls the app's own GET endpoints with your session password; it changes nothing, starts no scan and prints COUNTS, never individual predictions.

    .\verify_after_deploy.ps1                      (run on the VPS in PowerShell; -Port 8000 by default)

  Checks: production services (USStockReader answers, max_inflight 2, Options Flow OK, Engine20 route OK), the monitor's state (scheduler mode, zero provider requests while cache-only), the 14-day calendar it tracks,
  the freshness / status label of every model on every event, and that no model source is an external AI service. Ends with PASS / WARN / FAIL lines.
#>
[CmdletBinding()]
param(
  [string]$App = 'C:\apps\us-stock-reader',
  [int]$Port = 8000,
  [int]$HorizonDays = 15
)
$ErrorActionPreference = 'Stop'
$EnvFile = Join-Path $App 'backend\.env'
$script:fail = 0; $script:warn = 0
function Say([string]$lvl, [string]$m) { Write-Host ("{0,-5} {1}" -f $lvl, $m); if ($lvl -eq 'FAIL') { $script:fail++ } elseif ($lvl -eq 'WARN') { $script:warn++ } }
$m = Select-String -Path $EnvFile -Pattern '^\s*ACCESS_PASSWORD\s*=(.*)$' | Select-Object -First 1
if (-not $m) { throw 'ACCESS_PASSWORD not found in .env' }
$pw = $m.Matches[0].Groups[1].Value.Trim().Trim([char]34).Trim([char]39); $h = @{ Authorization = "Bearer $pw" }
function Get-Json([string]$path, $headers) {
  # The application answers ANY unknown path with its web page (HTTP 200, text/html). A reply that is not JSON is therefore reported as "not data", never parsed.
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:$Port" + $path) -Headers $headers -TimeoutSec 60
    $ct = [string]$r.Headers['Content-Type']
    if ($ct -notmatch 'json') { Write-Host ("DIAG  {0} -> HTTP {1}, content-type '{2}', not JSON (the route is not part of the installed version, or the server returned its web page)" -f $path, [int]$r.StatusCode, $ct); return @{ code = [int]$r.StatusCode; json = $null; notJson = $true; ctype = $ct } }
    try { return @{ code = [int]$r.StatusCode; json = (ConvertFrom-Json $r.Content); notJson = $false; ctype = $ct } } catch { Write-Host ("DIAG  {0} -> HTTP {1}, content-type '{2}', JSON could not be parsed: {3}" -f $path, [int]$r.StatusCode, $ct, $_.Exception.Message); return @{ code = [int]$r.StatusCode; json = $null; notJson = $true; ctype = $ct } }
  } catch { if ($_.Exception.Response) { return @{ code = [int]$_.Exception.Response.StatusCode.value__; json = $null; notJson = $false; ctype = '' } } else { throw } }
}
Write-Host ("Earnings Monitor verification  " + (Get-Date).ToUniversalTime().ToString('o'))

# 1. existing production services
$fm = Get-Json '/api/admin/flow-metrics' $h
if ($fm.code -eq 200 -and $fm.json.max_inflight -eq 2) { Say 'PASS' 'USStockReader answers; max_inflight = 2' } else { Say 'FAIL' "flow-metrics HTTP $($fm.code), max_inflight $($fm.json.max_inflight)" }
$of = Get-Json '/api/options/flow/AAPL?limit=5' $h
if ($of.code -eq 200 -and $of.json.status -eq 'OK') { Say 'PASS' 'Options Flow status OK' } else { Say 'FAIL' "Options Flow HTTP $($of.code)" }
$e20 = Get-Json '/api/smart-move/engine20/latest' $h
if ($e20.code -eq 200 -and $e20.json.status -eq 'OK' -and $e20.json.is_live_signal -eq $false) { Say 'PASS' "Engine20 snapshot route OK (historical snapshot, counts $($e20.json.counts.BUY)/$($e20.json.counts.SELL)/$($e20.json.counts.NO_TRADE)/$($e20.json.counts.NO_VALID_PREDICTION))" } else { Say 'FAIL' "Engine20 route HTTP $($e20.code)" }
$anon = Get-Json '/api/earnings-monitor/status' @{}
if ($anon.code -eq 401) { Say 'PASS' 'monitor API requires login (401 without it)' } else { Say 'FAIL' "monitor API answered HTTP $($anon.code) without login" }

# 2. the monitor
$st = Get-Json '/api/earnings-monitor/status' $h
if ($st.code -ne 200) { Say 'FAIL' "monitor status HTTP $($st.code)"; Write-Host "RESULT: FAIL"; exit 1 }
$s = $st.json
Say 'INFO' "scheduler: $($s.scheduling); gate: $(@($s.gate_reasons) -join ', '); last successful scan: $($s.last_any_success); provider requests today: $($s.provider_requests_today)"
if ($s.scheduling -eq 'OFF') { Say 'INFO' 'the scheduler is OFF (expected until you give the cache-only order)' }
if ($s.queue) { Say 'INFO' ("jobs: " + (($s.queue.PSObject.Properties | ForEach-Object { $_.Name + '=' + $_.Value }) -join ', ') + "; last success: " + (($s.last_success.PSObject.Properties | Where-Object { $_.Value } | ForEach-Object { $_.Name + ' ' + $_.Value }) -join ' | ')) }
if ($s.queue -and $s.queue.RUNNING -and -not $s.last_success.ELIGIBILITY_CHECK) { Say 'WARN' 'the first eligibility scan is still running; the grid below may be partial. Run this check again in a few minutes.' }
if ($s.scheduling -eq 'CACHE_ONLY' -and [int]$s.provider_requests_today -ne 0) { Say 'FAIL' "cache-only but $($s.provider_requests_today) provider request(s) counted today" }
if ($s.scheduling -eq 'CACHE_ONLY' -and [int]$s.provider_requests_today -eq 0) { Say 'PASS' 'cache-only: zero provider requests counted' }
if ($s.scheduling -eq 'REFRESH') { Say 'WARN' 'provider refresh is approved: stage 1 should be cache-only' }

# 3. the rolling calendar (the same list the Upcoming page shows)
$up = Get-Json '/api/earnings-monitor/upcoming' $h
if ($up.code -eq 200 -and $up.json.status -eq 'NO_DATA') { Say 'INFO' 'upcoming: no monitor data yet (the monitor has not run on this server)' }
elseif ($up.code -eq 200 -and $up.json.status -eq 'OK') {
  $rows = @($up.json.rows)
  if ($rows.Count -eq 0) { Say 'WARN' 'no upcoming events tracked yet (the model caches may be cold, or the first scan has not run)' }
  else {
    $days = @($rows | ForEach-Object { [int]$_.days_to })
    $ver = $rows | Group-Object verification | ForEach-Object { "$($_.Name)=$($_.Count)" }
    $elig = @($rows | Where-Object { $_.eligible }).Count
    $nAll = if ($up.json.summary) { [int]$up.json.summary.events } else { $rows.Count }
    $maxAll = if ($up.json.summary) { [int]$up.json.summary.max_days_ahead } else { ($days | Measure-Object -Maximum).Maximum }
    Say 'PASS' "tracking $nAll upcoming events, 0 to $maxAll days ahead (of the $($rows.Count) listed on this page: $elig eligible; calendar check: $($ver -join ', '))"
    $beyond = @($rows | Where-Object { [int]$_.days_to -gt $HorizonDays }).Count
    if ($beyond) { Say 'INFO' "$beyond event(s) are beyond the $HorizonDays-day eight-model window: they are on the 30-day calendar and are listed, never scored" }
    $conf = @($rows | Where-Object { $_.verification -eq 'CONFLICT' }).Count
    if ($conf) { Say 'WARN' "$conf event(s) where the calendar vendors disagree" }
    if ($s.scheduling -ne 'REFRESH') { Say 'INFO' 'vendor verification happens only on a provider calendar refresh; in cache-only mode events show UNVERIFIED by design' }
  }
} else { Say 'FAIL' "upcoming HTTP $($up.code)" }

# 4. every model on every event: label / freshness
$md = Get-Json '/api/earnings-monitor/models' $h
if ($md.code -eq 200 -and $md.json.status -eq 'NO_DATA') { Say 'INFO' 'models: no monitor data yet' }
elseif ($md.code -eq 200 -and $md.json.status -eq 'OK') {
  $cells = @(); foreach ($r in @($md.json.rows)) { foreach ($p in $r.cells.PSObject.Properties) { $cells += [pscustomobject]@{ model = $p.Name; label = $p.Value.label; source = $p.Value.source; age = $p.Value.age_h } } }
  if (-not $cells.Count) { Say 'WARN' 'no model cells yet' }
  else {
    $by = $cells | Group-Object label | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Count)" }
    Say 'PASS' "model labels across $(@($md.json.rows).Count) events: $($by -join ', ')"
    $perModel = $cells | Group-Object model | ForEach-Object { $calls = @($_.Group | Where-Object { $_.label -in 'BUY', 'SELL', 'NEUTRAL', 'NO_TRADE' }).Count; "$($_.Name): $calls call(s) of $($_.Count)" }
    Say 'INFO' ("per model: " + ($perModel -join ' | '))
    $stale = @($cells | Where-Object { $_.label -eq 'STALE' }).Count
    if ($stale) { Say 'WARN' "$stale stale call(s) (older than $($md.json.stale_after_h) h)" }
    $src = $cells | Where-Object { $_.source } | Group-Object source | ForEach-Object { "$($_.Name)=$($_.Count)" }
    Say 'INFO' ("sources of the calls: " + ($src -join ', '))
  }
} else { Say 'FAIL' "models HTTP $($md.code)" }

# 5. the nine sources
$so = Get-Json '/api/earnings-monitor/sources' $h
if ($so.code -eq 200) {
  $ms = @($so.json.models)
  $ext = @($ms | Where-Object { $_.external_ai_service -ne $false }).Count
  $warm = @($ms | Where-Object { $_.stored_predictions_now }).Count
  if ($ms.Count -eq 9 -and $ext -eq 0) { Say 'PASS' "9 model sources connected, none is an external AI service; $warm of 9 hold genuine stored predictions right now" } else { Say 'FAIL' "sources: $($ms.Count) listed, $ext flagged external" }
  foreach ($x in @($ms | Where-Object { -not $_.stored_predictions_now })) { Say 'INFO' "no stored prediction yet: $($x.label) -> $($x.needs_authorised_execution)" }
} else { Say 'FAIL' "sources HTTP $($so.code)" }

# 6. live-cache compatibility (read-only; field names and counts only, never values)
$cp = Get-Json '/api/earnings-monitor/cache-probe' $h
if ($cp.code -eq 200 -and -not $cp.notJson) {
  $j = $cp.json
  $wrong = @('uw_news', 'uw_flow', 'uw_calendar', 'earn_equity', 'finviz_cache' | Where-Object { $j.$_ -and [int]$j.$_.rows_sampled -gt 0 -and $j.$_.compatible -eq $false })
  $nos = @('uw_news', 'uw_flow', 'uw_calendar', 'earn_equity', 'finviz_cache' | Where-Object { $j.$_ -and [int]$j.$_.rows_sampled -eq 0 })
  if ($wrong.Count) { Say 'WARN' ("cache probe: {0} reader(s) found samples with a field missing: {1}. The monitor skips what it cannot read and never invents data." -f $wrong.Count, ($wrong -join ', ')) }
  elseif ($nos.Count) { Say 'INFO' ("cache probe: nothing found WRONG; {0} cache(s) had no samples to judge yet: {1}" -f $nos.Count, ($nos -join ', ')) }
  else { Say 'PASS' 'cache probe: every reader matches the shapes in the live caches' }
  # A cache that holds NO samples right now (for example just after a restart: the in-process caches are empty until the warmers refill them) cannot be judged; it is not the same as a wrong field.
  foreach ($k in 'uw_news', 'uw_flow', 'uw_calendar', 'earn_equity', 'finviz_cache') { $x = $j.$k; if ($x) {
    if ([int]$x.rows_sampled -eq 0) { Say 'INFO' ("probe {0}: NO SAMPLES in the cache right now, so compatibility cannot be judged (run again in 10 minutes)" -f $k) }
    else { Say $(if ($x.compatible -eq $false) { 'WARN' } else { 'INFO' }) ("probe {0}: compatible={1} samples={2} missing fields=[{3}]" -f $k, $x.compatible, $x.rows_sampled, ((@($x.missing_fields)) -join ',')) } } }
  if ($j.earn_equity -and $null -ne $j.earn_equity.symbols_sampled) { Say 'INFO' ("equity inputs: {0} of {1} sampled symbols found ({2} in process memory, {3} from the shared database tier)" -f $j.earn_equity.symbols_cached, $j.earn_equity.symbols_sampled, $j.earn_equity.symbols_in_process_memory, $j.earn_equity.symbols_from_database_tier) }
  if ($j.model_scan_caches) { Say 'INFO' ("model scan caches present: {0}; absent: {1}" -f ((@($j.model_scan_caches.present)) -join ','), ((@($j.model_scan_caches.absent)) -join ',')) }
  if ($j.uw_calendar) { Say 'INFO' ("30-day calendar cache: {0} of {1} weekday dates cached" -f $j.uw_calendar.dates_cached, $j.uw_calendar.dates_wanted) }
} elseif ($cp.code -eq 404 -or $cp.notJson) { Say 'WARN' 'cache-probe is not part of the installed version (the update did not take effect, or the server was not restarted)' } else { Say 'WARN' "cache-probe HTTP $($cp.code)" }

# 7. scanner health: timings, stuck and duplicate jobs
$hl = Get-Json '/api/earnings-monitor/health' $h
if ($hl.code -eq 200 -and -not $hl.notJson) {
  $e = @($hl.json.recent_runs | Where-Object { $_.kind -eq 'ELIGIBILITY_CHECK' -and $null -ne $_.duration_ms }) | Select-Object -First 1
  if ($e) { Say 'INFO' ("latest ELIGIBILITY_CHECK: {0} ms, {1} events, waited {2} ms" -f $e.duration_ms, $e.events_processed, $e.queue_wait_ms) } else { Say 'INFO' 'no timed ELIGIBILITY_CHECK yet (the next 30-minute slot records one)' }
  if ($hl.json.healthy) { Say 'PASS' 'scanner health: no stuck jobs, no duplicate events or predictions, nothing dead' } else { Say 'FAIL' ("scanner health reports a problem: stuck={0} duplicates={1} dead={2}" -f @($hl.json.stuck_jobs).Count, @($hl.json.duplicate_active_events).Count, @($hl.json.dead_jobs).Count) }
  if ($null -ne $hl.json.approval_days_left) { Say $(if ($hl.json.approval_days_left -le 3) { 'WARN' } else { 'INFO' }) ("owner approval ends in {0} day(s)" -f $hl.json.approval_days_left) }
} elseif ($hl.code -eq 404 -or $hl.notJson) { Say 'WARN' 'health view is not part of the installed version' } else { Say 'WARN' "health HTTP $($hl.code)" }


# 8. rolling 30-day calendar. The server pages its lists, so the totals come from the server's own summary of ALL tracked events (the same numbers the Upcoming page shows); older servers send every row and the totals are counted here.
$u2 = Get-Json '/api/earnings-monitor/upcoming?limit=1000' $h
if ($u2.code -eq 200 -and -not $u2.notJson -and $u2.json) {
  $rows = @($u2.json.rows); $S = $u2.json.summary
  $hz = [int]$u2.json.horizon_days; $mw = [int]$u2.json.model_window_days
  if ($S) { $n = [int]$S.events; $ndates = [int]$S.dates; $maxd = [int]$S.max_days_ahead; $inWin = [int]$S.in_window; $outWin = [int]$S.outside_window; $withCall = [int]$S.with_fresh_call; $noCall = [int]$S.in_window_without_call; $dateRows = @($u2.json.by_date) }
  else {
    $n = $rows.Count; $ndates = @($rows | Group-Object earnings_date).Count; $maxd = if ($rows.Count) { ($rows | Measure-Object -Property days_to -Maximum).Maximum } else { 0 }
    $inWin = @($rows | Where-Object { -not $_.out_of_window }).Count; $outWin = $n - $inWin; $withCall = @($rows | Where-Object { $_.fresh_predictions -ge 1 }).Count; $noCall = @($rows | Where-Object { -not $_.out_of_window -and $_.fresh_predictions -lt 1 }).Count
    $dateRows = @($rows | Group-Object earnings_date | ForEach-Object { [pscustomobject]@{ date = $_.Name; events = $_.Count; in_window = @($_.Group | Where-Object { -not $_.out_of_window }).Count; with_fresh_call = @($_.Group | Where-Object { $_.fresh_predictions -ge 1 }).Count } })
  }
  Say 'INFO' ("calendar: {0} distinct events on {1} market dates, 0 to {2} days ahead (horizon {3} d, eight-model window {4} d)" -f $n, $ndates, $maxd, $hz, $mw)
  Say 'INFO' ("inside the {0}-day model window: {1} events ({2} with at least one fresh model call, {3} with none); outside the window: {4} events (listed, never scored)" -f $mw, $inWin, $withCall, $noCall, $outWin)
  if ($S -and $S.fresh_calls_per_model) { Say 'INFO' ("fresh calls per model: " + ((($S.fresh_calls_per_model.PSObject.Properties) | ForEach-Object { "{0}={1}" -f $_.Name, $_.Value }) -join ', ')) }
  if ($maxd -lt ($hz - 5)) { Say 'WARN' ("the calendar reaches only {0} days ahead, not {1}: dates beyond that are not in any cache the application holds yet (the first scan after an update picks up what is cached; a calendar refresh is Stage D)" -f $maxd, $hz) } else { Say 'PASS' ("the calendar reaches {0} days ahead" -f $maxd) }
  Say 'INFO' 'cached dates are not proof of complete coverage: the counts below are events actually present for each market date'
  $have = @{}; foreach ($g in $dateRows) { $have[[string]$g.date] = [int]$g.events }
  $today = (Get-Date).ToUniversalTime().Date; $gaps = @()
  for ($i = 0; $i -le $hz; $i++) { $d = $today.AddDays($i); if ($d.DayOfWeek -ne 'Saturday' -and $d.DayOfWeek -ne 'Sunday' -and -not $have.ContainsKey($d.ToString('yyyy-MM-dd'))) { $gaps += $d.ToString('yyyy-MM-dd') } }
  Say 'INFO' ("weekdays with no event listed (a market holiday, or not cached yet): {0}" -f $(if ($gaps.Count) { $gaps -join ', ' } else { 'none' }))
  foreach ($g in ($dateRows | Sort-Object date | Select-Object -First 31)) { Write-Host ("      {0}  events={1,5}  in window={2,5}  with a fresh call={3,5}" -f $g.date, $g.events, $g.in_window, $g.with_fresh_call) }
} elseif ($u2.notJson) { Say 'WARN' 'upcoming: the server returned a web page, not data' } else { Say 'WARN' "upcoming HTTP $($u2.code)" }

# 9. Smart Money Move (first page of up to 200 events; the server's own totals are above): the market date of every result, its origin, and the real reason when there is none
$sm = Get-Json '/api/earnings-monitor/smart-move' $h
if ($sm.code -eq 200 -and -not $sm.notJson -and $sm.json) {
  $srows = @($sm.json.rows)
  if ($sm.json.coverage) { Say 'INFO' ("latest {0}: made {1}, scored {2} event(s) dated {3}" -f $sm.json.coverage.source, $sm.json.coverage.generated_at, $sm.json.coverage.rows, ($sm.json.coverage.event_dates -join ', ')) } else { Say 'INFO' 'no engine run or snapshot has been read yet' }
  foreach ($g in ($srows | Group-Object earnings_date | Sort-Object Name | Select-Object -First 8)) {
    $calls = @($g.Group | Where-Object { $_.label -in 'BUY', 'SELL', 'NO_TRADE', 'NEUTRAL' }).Count
    $codes = ($g.Group | Where-Object { $_.reason } | Group-Object { $_.reason.code } | ForEach-Object { "{0} x{1}" -f $_.Name, $_.Count }) -join '; '
    $orig = ($g.Group | Group-Object origin | ForEach-Object { "{0} x{1}" -f $_.Name, $_.Count }) -join '; '
    Write-Host ("      {0}  events={1,4}  with a call={2,3}  origin: {3}  | reasons: {4}" -f $g.Name, $g.Count, $calls, $orig, $(if ($codes) { $codes } else { 'none needed' }))
  }
  $wrong = @($srows | Where-Object { $_.signal -and $_.details -and $_.details.event_date -and $_.details.event_date -ne $_.earnings_date })
  if ($wrong.Count) { Say 'FAIL' ("{0} Smart Money Move call(s) are attached to an event of a DIFFERENT report date" -f $wrong.Count) } else { Say 'PASS' 'every Smart Money Move call belongs to the report date of its event (no cross-date matches)' }
  $ng = @($srows | Where-Object { $_.origin -eq 'NOT_GENERATED' -and $_.signal -and $_.source -in 'smart_move_snapshot', 'smart_move_engine' }).Count
  if ($ng) { Say 'FAIL' ("{0} event(s) are labelled NOT GENERATED although the snapshot / engine produced their call" -f $ng) }
} elseif ($sm.notJson) { Say 'WARN' 'smart-move: the server returned a web page, not data' } else { Say 'WARN' "smart-move HTTP $($sm.code)" }

Remove-Variable pw, h
Write-Host ("RESULT: " + $(if ($script:fail) { 'FAIL' } elseif ($script:warn) { 'PASS with warnings' } else { 'PASS' }) + "  ($script:fail fail, $script:warn warn)")
if ($script:fail) { exit 1 }
