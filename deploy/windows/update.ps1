<#
.SYNOPSIS
    Pull, rebuild, test, cut over with new trade entries LOCKED.

.DESCRIPTION
    Run it only while new entries are impossible by time (a weekend, or outside 04:00-20:00 ET) and no position is
    open: the preflight below refuses otherwise, and there is no switch to skip it.

    1. Records the commit that is running now (the rollback reference).
    2. Stages the TARGET commit in a separate worktree and runs its preflight: release_check.py (mandatory 15-minute
       confirmation, stale-data protection, 3-connection cap, entry guards), the time window and the open positions.
       If an already-installed release_check.py exists it must pass too.
    3. Pulls, installs dependencies, builds the frontend, runs the tests -- stopping before anything is restarted if
       any of them fail.
    4. release_tool.py cutover: stops the service, installs the entry lock (a first install starts LOCKED), starts the
       service and waits for /safety. Entries stay LOCKED.
    5. You unlock them with `release_tool.py unlock`, which refuses unless the RUNNING service reports that it keeps
       the mandatory 15-minute confirmation.

    See docs\release_and_rollback.md.
#>

param(
    [string]$ServiceName = "USStockReader",
    [int]$Port = 8000,
    [string]$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
)

$ErrorActionPreference = "Stop"
Set-Location $Root
$python = "$Root\backend\.venv\Scripts\python.exe"
$url = "http://127.0.0.1:$Port"

$previous = (git rev-parse HEAD).Trim()
Write-Host "==> current commit (rollback reference): $previous"
git fetch --quiet
$target = (git rev-parse "@{u}").Trim()
Write-Host "==> target commit: $target"

# --- stage the target and judge it BEFORE touching the running tree --------------------------------------------
$stage = Join-Path $env:TEMP ("trdgo-stage-" + $target.Substring(0, 12))
if (Test-Path $stage) { git worktree remove --force $stage | Out-Null }
git worktree add --detach $stage $target | Out-Null
try {
    # The staged copy has no .env, so it cannot find the database by itself: give the preflight the running site's
    # database settings (only these two, for this one process; they are never printed) so it can READ the open positions
    # and signals. Without them it refuses ("could not read open positions"), which is safe but would block every window.
    foreach ($ln in @(Get-Content "$Root\backend\.env" -ErrorAction SilentlyContinue)) {
        if ($ln -match '^\s*(DATABASE_URL|APP_ENV)\s*=\s*(.*)$') { Set-Item -Path ("Env:" + $Matches[1]) -Value $Matches[2].Trim().Trim('"').Trim("'") }
    }
    & $python "$stage\backend\release_tool.py" preflight --repo $Root --target $target
    $pre = $LASTEXITCODE
    Remove-Item Env:DATABASE_URL, Env:APP_ENV -ErrorAction SilentlyContinue
    if ($pre -ne 0) {
        Write-Host "Preflight refused the target -- nothing was changed." -ForegroundColor Red
        exit 1
    }
    if (Test-Path "$Root\backend\release_check.py") {
        & $python "$Root\backend\release_check.py" $target
        if ($LASTEXITCODE -ne 0) {
            Write-Host "The installed release check refuses the target -- nothing was changed." -ForegroundColor Red
            exit 1
        }
    }
} finally {
    git worktree remove --force $stage | Out-Null
}

Write-Host "==> pulling"
git pull --ff-only

Write-Host "==> python dependencies"
& "$Root\backend\.venv\Scripts\pip.exe" install -q -r "$Root\backend\requirements.txt"

Write-Host "==> frontend"
Push-Location "$Root\frontend"
npm ci --silent
npm run build
$buildFailed = $LASTEXITCODE
Pop-Location
if ($buildFailed -ne 0) {
    Write-Host "Frontend build failed -- the running service was left alone." -ForegroundColor Red
    exit 1
}

Write-Host "==> tests"
Push-Location "$Root\backend"
& $python -m pytest -q
$failed = $LASTEXITCODE
Pop-Location
if ($failed -ne 0) {
    Write-Host "Tests failed -- the running service was left alone." -ForegroundColor Red
    exit 1
}

Write-Host "==> cut-over (stop, entry lock, start, verify) with entries LOCKED"
Push-Location "$Root\backend"
& $python release_tool.py cutover --repo $Root --target $target --service $ServiceName --url $url
$cut = $LASTEXITCODE
Pop-Location
if ($cut -ne 0) {
    Write-Host "Cut-over did not complete. Entries stay locked. Read the message above and docs\release_and_rollback.md." -ForegroundColor Red
    exit 1
}
Write-Host "Running with new entries LOCKED. When you have checked it:" -ForegroundColor Green
Write-Host "    cd $Root\backend ; $python release_tool.py unlock --url $url"
