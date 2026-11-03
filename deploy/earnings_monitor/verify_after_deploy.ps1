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
  try { $r = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:$Port" + $path) -Headers $headers -TimeoutSec 60; return @{ code = [int]$r.StatusCode; json = (ConvertFrom-Json $r.Content) } }
  catch { if ($_.Exception.Response) { return @{ code = [int]$_.Exception.Response.StatusCode.value__; json = $null } } else { throw } }
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
if ($s.scheduling -eq 'CACHE_ONLY' -and [int]$s.provider_requests_today -ne 0) { Say 'FAIL' "cache-only but $($s.provider_requests_today) provider request(s) counted today" }
if ($s.scheduling -eq 'CACHE_ONLY' -and [int]$s.provider_requests_today -eq 0) { Say 'PASS' 'cache-only: zero provider requests counted' }
if ($s.scheduling -eq 'REFRESH') { Say 'WARN' 'provider refresh is approved: stage 1 should be cache-only' }

# 3. the 14-day calendar
$up = Get-Json '/api/earnings-monitor/upcoming' $h
if ($up.code -eq 200 -and $up.json.status -eq 'OK') {
  $rows = @($up.json.rows)
  if ($rows.Count -eq 0) { Say 'WARN' 'no upcoming events tracked yet (the model caches may be cold, or the first scan has not run)' }
  else {
    $days = @($rows | ForEach-Object { [int]$_.days_to })
    $ver = $rows | Group-Object verification | ForEach-Object { "$($_.Name)=$($_.Count)" }
    $elig = @($rows | Where-Object { $_.eligible }).Count
    Say 'PASS' "tracking $($rows.Count) upcoming events, $(($days | Measure-Object -Minimum).Minimum) to $(($days | Measure-Object -Maximum).Maximum) days ahead; $elig eligible; calendar check: $($ver -join ', ')"
    if (($days | Measure-Object -Maximum).Maximum -gt $HorizonDays) { Say 'WARN' "some events are beyond the $HorizonDays-day horizon (shown as not eligible)" }
    $conf = @($rows | Where-Object { $_.verification -eq 'CONFLICT' }).Count
    if ($conf) { Say 'WARN' "$conf event(s) where the calendar vendors disagree" }
    if ($s.scheduling -ne 'REFRESH') { Say 'INFO' 'vendor verification happens only on a provider calendar refresh; in cache-only mode events show UNVERIFIED by design' }
  }
} else { Say 'FAIL' "upcoming HTTP $($up.code)" }

# 4. every model on every event: label / freshness
$md = Get-Json '/api/earnings-monitor/models' $h
if ($md.code -eq 200 -and $md.json.status -eq 'OK') {
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

Remove-Variable pw, h
Write-Host ("RESULT: " + $(if ($script:fail) { 'FAIL' } elseif ($script:warn) { 'PASS with warnings' } else { 'PASS' }) + "  ($script:fail fail, $script:warn warn)")
if ($script:fail) { exit 1 }
