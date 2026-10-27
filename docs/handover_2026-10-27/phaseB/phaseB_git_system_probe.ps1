# TRDGO-US Phase B / stage B1b: run read-only git commands ONCE as SYSTEM through a temporary scheduled task.
# Creates: one temporary scheduled task (removed at the end, removal verified) and two small files in $Dir.
# Changes nothing else: no git config, no safe.directory, no repository file, no service, no database.
param([string]$Root = "C:\apps\us-stock-reader", [string]$Dir = "C:\audit-reports", [switch]$AsCurrentUser)   # -AsCurrentUser is only for rehearsal
$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$task = "TrdgoGitProbe_" + $stamp
$inner = Join-Path $Dir ("system_git_probe_inner_" + $stamp + ".ps1")
$out = Join-Path $Dir ("system_git_probe_" + $stamp + ".txt")
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $AsCurrentUser -and -not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { Write-Host "STOPPED, NOTHING CHANGED: run this in an elevated (Administrator) PowerShell." -ForegroundColor Red; exit 2 }
$git = (Get-Command git -ErrorAction SilentlyContinue).Source
if (-not $git) { Write-Host "STOPPED, NOTHING CHANGED: git not found." -ForegroundColor Red; exit 2 }
if (Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like "TrdgoGitProbe_*" }) { Write-Host "STOPPED, NOTHING CHANGED: a TrdgoGitProbe_* task already exists (leftover). Report it to me." -ForegroundColor Red; exit 2 }
$null = New-Item -ItemType Directory -Force -Path $Dir

# the program the task runs: read-only git, every exit code and output recorded
$body = @'
$ErrorActionPreference = "Continue"
$git = "__GIT__"; $root = "__ROOT__"; $out = "__OUT__"
function W($s) { Add-Content -Path $out -Value $s -Encoding ASCII }
W ("whoami=" + [Security.Principal.WindowsIdentity]::GetCurrent().Name)
W ("started=" + (Get-Date -Format s))
$r = & $git --no-optional-locks -C $root rev-parse HEAD 2>&1; $c = $LASTEXITCODE
W ("rev_parse_exit=" + $c); foreach ($l in @($r)) { W ("rev_parse_out=" + $l) }
$r = & $git --no-optional-locks -C $root status --porcelain 2>&1; $c = $LASTEXITCODE
W ("status_exit=" + $c); W ("status_line_count=" + @($r | Where-Object { $_ }).Count); foreach ($l in @($r)) { W ("status_out=" + $l) }
$r = & $git --no-optional-locks -C $root branch --show-current 2>&1; $c = $LASTEXITCODE
W ("branch_exit=" + $c); foreach ($l in @($r)) { W ("branch_out=" + $l) }
W "finished=1"
'@
$body = $body.Replace("__GIT__", $git).Replace("__ROOT__", $Root).Replace("__OUT__", $out)
Set-Content -Path $inner -Value $body -Encoding ASCII
$tk = $null; $er = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($inner, [ref]$tk, [ref]$er)
if ($er.Count) { Write-Host "STOPPED: probe program has a syntax error; nothing was registered." -ForegroundColor Red; exit 2 }

$act = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $inner + '"')
if ($AsCurrentUser) { $pri = New-ScheduledTaskPrincipal -UserId $id.Name -LogonType Interactive } else { $pri = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest }
$registered = $false; $taskResult = $null
try {
    Register-ScheduledTask -TaskName $task -Action $act -Principal $pri | Out-Null; $registered = $true
    Start-ScheduledTask -TaskName $task
    for ($i = 0; $i -lt 90; $i++) { Start-Sleep 1; if ((Get-ScheduledTask -TaskName $task).State -ne "Running" -and (Test-Path $out) -and (Select-String -Path $out -Pattern "^finished=1" -Quiet)) { break } }
    $taskResult = (Get-ScheduledTaskInfo -TaskName $task).LastTaskResult
} finally {
    if ($registered) { Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue }
}
$left = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -eq $task })

Write-Host ""; Write-Host ("--- recorded output (" + $out + ") ---")
if (Test-Path $out) { Get-Content $out } else { Write-Host "(no output file: the task did not run or could not write)" -ForegroundColor Red }
Write-Host ("task last result: " + $taskResult + "  (0 = ran to completion)")
Write-Host ""
if ($left.Count) { Write-Host ("CLEANUP FAILED: scheduled task still exists: " + $task + ". STOP and report this. Remove only on approval:  Unregister-ScheduledTask -TaskName " + $task + " -Confirm:`$false") -ForegroundColor Red; exit 3 }
Write-Host ("cleanup verified: scheduled task " + $task + " no longer exists") -ForegroundColor Green
$o = if (Test-Path $out) { Get-Content $out } else { @() }
$dub = @($o | Where-Object { $_ -match "dubious|detected|not a git|fatal" })
if ($dub.Count) { Write-Host "RESULT: git REFUSED or failed as the service account (see lines above). Send me this output." -ForegroundColor Red; exit 4 }
if (($o -contains "rev_parse_exit=0") -and ($o -contains "status_exit=0")) { Write-Host "RESULT: git works for this account on the repository (exit codes 0)." -ForegroundColor Green } else { Write-Host "RESULT: not confirmed (a git exit code was not 0 or output is missing). Send me this output." -ForegroundColor Yellow }
Write-Host ("kept for the record: " + $out + " and " + $inner)
