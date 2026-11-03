<#
  Stage 1 of the Earnings Monitor: CACHE-ONLY activation. Run it YOURSELF on the VPS, in an elevated PowerShell, ONLY after the monitor release is installed and verified, and only when you have given the separate order to switch it on.

  Cache-only means the scheduler runs and reads the model caches, the stored daily snapshots and the Engine20 snapshot. It makes NO provider request of any kind (no Unusual Whales, no Finnhub, no Finviz): the approval it writes has
  mode "cache_only", which the gate refuses to use for any provider call. Provider refresh and Smart Move collection stay OFF; they need their own, later approvals.

    .\activate_cache_only.ps1 -Action Activate                  asks for your name and a typed confirmation, edits .env (backed up), writes the approval, restarts USStockReader, verifies, rolls back by itself on failure
    .\activate_cache_only.ps1 -Action Deactivate                removes the approval and the .env switch, restarts, verifies

  Test switches (offline rehearsal only): -NoRestart -NoHealth -ApprovedBy <name> -SkipConfirm
#>
[CmdletBinding()]
param(
  [ValidateSet('Activate', 'Deactivate')][string]$Action = 'Activate',
  [string]$App = 'C:\apps\us-stock-reader',
  [string]$Service = 'USStockReader',
  [int]$Port = 8000,
  [int]$ValidDays = 14,
  [string]$ApprovedBy = '',
  [switch]$SkipConfirm,
  [switch]$NoRestart,
  [switch]$NoHealth
)
$ErrorActionPreference = 'Stop'
$Backend = Join-Path $App 'backend'
$EnvFile = Join-Path $Backend '.env'
$DataDir = Join-Path $App 'em_data'
$Approval = Join-Path $DataDir 'approval.json'

function Log([string]$m) { Write-Host ((Get-Date).ToUniversalTime().ToString('HH:mm:ss') + 'Z  ' + $m) }
function Get-Pw {
  $m = Select-String -Path $EnvFile -Pattern '^\s*ACCESS_PASSWORD\s*=(.*)$' | Select-Object -First 1
  if (-not $m) { throw 'ACCESS_PASSWORD not found in .env' }
  $m.Matches[0].Groups[1].Value.Trim().Trim([char]34).Trim([char]39)
}
function Get-Json([string]$path, $headers) {
  try { $r = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:$Port" + $path) -Headers $headers -TimeoutSec 40; return @{ code = [int]$r.StatusCode; body = $r.Content } }
  catch { if ($_.Exception.Response) { return @{ code = [int]$_.Exception.Response.StatusCode.value__; body = '' } } else { throw } }
}
function Test-Base {
  if ($NoHealth) { return }
  $pw = Get-Pw; $h = @{ Authorization = "Bearer $pw" }
  $m = Get-Json '/api/admin/flow-metrics' $h
  if ($m.code -ne 200 -or ((ConvertFrom-Json $m.body).max_inflight -ne 2)) { throw "max_inflight is not 2 (HTTP $($m.code))" }
  $o = Get-Json '/api/options/flow/AAPL?limit=5' $h
  if ($o.code -ne 200 -or ((ConvertFrom-Json $o.body).status -ne 'OK')) { throw "Options Flow is not OK (HTTP $($o.code))" }
  $e = Get-Json '/api/smart-move/engine20/latest' $h
  if ($e.code -ne 200) { throw "the Engine20 snapshot route answers HTTP $($e.code)" }
  Log 'health: max_inflight = 2, Options Flow OK, Engine20 snapshot route OK'
}
function Get-Status {
  $pw = Get-Pw; $h = @{ Authorization = "Bearer $pw" }
  $s = Get-Json '/api/earnings-monitor/status' $h
  if ($s.code -ne 200) { throw "monitor status answers HTTP $($s.code)" }
  ConvertFrom-Json $s.body
}
function Restart-Svc {
  if ($NoRestart) { Log 'restart skipped (-NoRestart)'; return }
  $nssm = (Get-Command nssm -ErrorAction SilentlyContinue)
  if (-not $nssm) { throw 'nssm not found in PATH' }
  Log "restarting $Service"; & nssm restart $Service | Out-Null
  $until = (Get-Date).AddSeconds(120)
  do { Start-Sleep -Seconds 3; $up = (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) } while (-not $up -and (Get-Date) -lt $until)
  if (-not $up) { throw "port $Port is not listening 120 s after the restart" }
  Start-Sleep -Seconds 5
  Log "$Service is listening on $Port"
}
function Remove-Switch([string]$envBackup) {
  if ($envBackup -and (Test-Path $envBackup)) { Copy-Item $envBackup $EnvFile -Force } else {
    $keep = [IO.File]::ReadAllLines($EnvFile) | Where-Object { $_ -notmatch '^\s*EARNINGS_MONITOR_(ENABLED|DIR)\s*=' }
    [IO.File]::WriteAllLines($EnvFile, $keep, (New-Object Text.UTF8Encoding($false)))
  }
  if (Test-Path $Approval) { Remove-Item $Approval -Force }
}

if ($Action -eq 'Activate') {
  Log "PREFLIGHT  app=$App"
  if (-not (Test-Path (Join-Path $Backend 'earnings_monitor'))) { throw 'the Earnings Monitor release is not installed (backend\earnings_monitor is missing)' }
  if (-not $NoRestart) { if ((Get-Service $Service).Status -ne 'Running') { throw "$Service is not Running" } }
  $envText = [IO.File]::ReadAllText($EnvFile)
  if ($envText -match '(?m)^\s*EARNINGS_MONITOR_(ENABLED|APPROVAL|DIR)\s*=') { throw '.env already sets an EARNINGS_MONITOR_* variable' }
  if (Test-Path $Approval) { throw "an approval file already exists: $Approval" }
  if ($ValidDays -lt 1 -or $ValidDays -gt 30) { throw '-ValidDays must be 1..30' }
  Test-Base
  if (-not $NoHealth) { $s0 = Get-Status; if ($s0.scheduling -ne 'OFF') { throw "the monitor already reports scheduling $($s0.scheduling)" } }
  if (-not $ApprovedBy) { $ApprovedBy = Read-Host 'Type your name to approve CACHE-ONLY scanning (no provider requests)' }
  if ([string]::IsNullOrWhiteSpace($ApprovedBy)) { throw 'no name typed - nothing changed' }
  if (-not $SkipConfirm) { if ((Read-Host 'Type exactly  I APPROVE CACHE-ONLY  to continue') -ne 'I APPROVE CACHE-ONLY') { throw 'not confirmed - nothing changed' } }
  Log 'PREFLIGHT OK'

  $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss_fff'); $bk = Join-Path $App "_backup_cache_only_$stamp"
  New-Item -ItemType Directory $bk | Out-Null
  Copy-Item $EnvFile (Join-Path $bk 'env.bak'); Log "backup written: $bk"
  try {
    New-Item -ItemType Directory -Force $DataDir | Out-Null
    $now = (Get-Date).ToUniversalTime()
    $ap = [ordered]@{ approved_by = $ApprovedBy.Trim(); approved_at = $now.ToString('yyyy-MM-ddTHH:mm:ss+00:00'); expires_at = $now.AddDays($ValidDays).ToString('yyyy-MM-ddTHH:mm:ss+00:00'); mode = 'cache_only'; scheduling_approved = $true }
    [IO.File]::WriteAllText($Approval, ($ap | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    $add = ""; if ($envText.Length -gt 0 -and -not $envText.EndsWith("`n")) { $add = "`r`n" }
    [IO.File]::WriteAllText($EnvFile, $envText + $add + "EARNINGS_MONITOR_ENABLED=1`r`nEARNINGS_MONITOR_DIR=$DataDir`r`n", (New-Object Text.UTF8Encoding($false)))
    Log 'approval written (cache_only) and .env switched on'
    Restart-Svc
    if (-not $NoHealth) {
      Test-Base
      $until = (Get-Date).AddSeconds(150); $s = $null
      do { Start-Sleep -Seconds 5; $s = Get-Status } while (($s.scheduling -ne 'CACHE_ONLY' -or -not $s.last_any_success) -and (Get-Date) -lt $until)
      if ($s.scheduling -ne 'CACHE_ONLY') { throw "the monitor reports scheduling $($s.scheduling): $($s.gate_reasons -join '; ')" }
      if (-not $s.last_any_success) { throw 'the scheduler is on but has not completed a scan in 150 s' }
      if ([int]$s.provider_requests_today -ne 0) { throw "provider requests today = $($s.provider_requests_today), expected 0 in cache-only mode" }
      Log "monitor: scheduling CACHE_ONLY, last successful scan $($s.last_any_success), provider requests today 0"
    }
    Log "ACTIVATE OK (stage 1: cache only). Backup kept at $bk. To stop: .\activate_cache_only.ps1 -Action Deactivate"
  } catch {
    $why = $_.Exception.Message
    Log "FAILURE: $why  -> rolling back automatically"
    Remove-Switch (Join-Path $bk 'env.bak'); Restart-Svc; Test-Base
    throw "ACTIVATION FAILED AND WAS ROLLED BACK: $why"
  }
} else {
  Log 'DEACTIVATE'
  Remove-Switch ''
  Restart-Svc
  Test-Base
  if (-not $NoHealth) { $s = Get-Status; if ($s.scheduling -ne 'OFF') { throw "the monitor still reports scheduling $($s.scheduling)" } }
  Log 'DEACTIVATE OK: the scheduler is OFF again (the stored history is kept in em_data)'
}
