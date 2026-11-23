# TRDGO-US database/service watchdog -> Telegram.
#
# WHY: on 2026-11-23 the Supabase project was suspended over an unpaid invoice. Every login
# returned HTTP 500 and every full-access account silently degraded to "View only", because the
# role lookup could not read the user row. It was users who noticed, not us. This is the thing
# that should have noticed.
#
# WHAT IT WATCHES: https://tradgo.us/health, which reports {"database":"connected"|"disconnected"}.
# It treats BOTH of these as down, because to a user they are the same thing:
#   - the app answers but says the database is disconnected   (what happened on the 23rd)
#   - the app does not answer at all                          (service stopped, site unreachable)
#
# RULES (owner, 2026-11-23):
#   - poll every 60 seconds              -> this script runs once per run; the task repeats it
#   - alert only after 2 CONSECUTIVE failures, so one blip does not page anyone (~2 min to alert)
#   - while it stays down, re-alert at most once every 30 minutes
#   - send exactly one RECOVERED message when it comes back
#
# IT CHANGES NOTHING. It reads one public URL and writes one small state file. It never touches
# the database, the service, or any Smart Move file.
#
# SECRETS ARE NOT IN THIS FILE and never go in the repository. It reads them from a file you
# create yourself on the server (see SECRETS below). If that file is missing the script says so
# and exits without alerting - it does not invent a destination.

[CmdletBinding()]
param(
    [string]$HealthUrl  = 'https://tradgo.us/health',
    [string]$SecretFile = 'C:\SmartMoveRun\watchdog_secrets.ps1',
    [string]$StateFile  = 'C:\SmartMoveRun\watchdog_state.json',
    [int]   $FailuresBeforeAlert = 2,
    [int]   $RealertMinutes      = 30,
    [switch]$TestMessage
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- secrets
if (-not (Test-Path -LiteralPath $SecretFile)) {
    Write-Output "NO SECRETS: $SecretFile does not exist. Create it (see the kit notes) with your bot token and chat id. Nothing was sent."
    exit 2
}
. $SecretFile   # must define $TelegramBotToken and $TelegramChatId
if ([string]::IsNullOrWhiteSpace($TelegramBotToken) -or [string]::IsNullOrWhiteSpace($TelegramChatId)) {
    Write-Output "NO SECRETS: $SecretFile is missing TelegramBotToken or TelegramChatId. Nothing was sent."
    exit 2
}

function Send-Telegram([string]$text) {
    # -Body as a hashtable so PowerShell encodes it; the token never appears in output.
    $uri = "https://api.telegram.org/bot$TelegramBotToken/sendMessage"
    try {
        $null = Invoke-RestMethod -Uri $uri -Method Post -TimeoutSec 20 -Body @{
            chat_id = $TelegramChatId; text = $text; disable_web_page_preview = 'true'
        }
        return $true
    } catch {
        Write-Output "TELEGRAM SEND FAILED: $($_.Exception.Message)"
        return $false
    }
}

if ($TestMessage) {
    $ok = Send-Telegram "TRDGO watchdog: test message. If you can read this, the bot token and chat id are correct."
    Write-Output $(if ($ok) { 'TEST SENT' } else { 'TEST FAILED' })
    exit $(if ($ok) { 0 } else { 1 })
}

# ---------------------------------------------------------------- state
$state = @{ fails = 0; down = $false; lastAlert = $null }
if (Test-Path -LiteralPath $StateFile) {
    try {
        $raw = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
        $state.fails     = [int]$raw.fails
        $state.down      = [bool]$raw.down
        $state.lastAlert = $raw.lastAlert
    } catch { }   # a corrupt state file must not stop the watchdog; start from zero
}

# ---------------------------------------------------------------- the check
$healthy = $false
$reason  = ''
try {
    $r = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 20 -UseBasicParsing
    $body = $r.Content | ConvertFrom-Json
    if ($body.database -eq 'connected' -and $body.status -eq 'ok') {
        $healthy = $true
    } else {
        $reason = "the site answered but reports database=$($body.database), status=$($body.status)"
    }
} catch {
    # no answer at all: service stopped, app crashed, or the site is unreachable
    $reason = "the site did not answer: $($_.Exception.Message)"
}

$nowStr = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')

if ($healthy) {
    if ($state.down) {
        $null = Send-Telegram "RECOVERED $nowStr`nTRDGO is back: the site answers and the database is connected again."
        Write-Output "RECOVERED - recovery message sent"
    } else {
        Write-Output "OK"
    }
    $state.fails = 0; $state.down = $false; $state.lastAlert = $null
} else {
    $state.fails = $state.fails + 1
    Write-Output "FAIL $($state.fails): $reason"

    if ($state.fails -ge $FailuresBeforeAlert) {
        $due = $true
        if ($state.lastAlert) {
            try { $due = ((Get-Date) - [datetime]$state.lastAlert).TotalMinutes -ge $RealertMinutes } catch { $due = $true }
        }
        if ($due) {
            $msg = "TRDGO IS DOWN  $nowStr`n$reason`n`nUsers will see HTTP 500 on login, and full-access accounts will show as View only.`nFailed checks in a row: $($state.fails)."
            if (Send-Telegram $msg) {
                $state.lastAlert = (Get-Date).ToString('o')
                Write-Output "ALERT SENT"
            }
        } else {
            Write-Output "still down; holding off (re-alert every $RealertMinutes min)"
        }
        $state.down = $true
    }
}

# write the state back; never let a write error crash the task
try {
    $dir = Split-Path -Parent $StateFile
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    $state | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
} catch { Write-Output "could not write $StateFile : $($_.Exception.Message)" }

exit 0
