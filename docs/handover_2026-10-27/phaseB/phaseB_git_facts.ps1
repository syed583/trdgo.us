# TRDGO-US Phase B / stage B1a: READ-ONLY facts about how git will behave for the service account. Changes nothing, runs nothing as SYSTEM.
$Root = "C:\apps\us-stock-reader"; $Svc = "USStockReader"
function Say($k, $v) { Write-Host ("{0,-34} {1}" -f $k, $v) }
$s = Get-CimInstance Win32_Service -Filter ("Name='" + $Svc + "'")
Say "service account" $s.StartName; Say "service state" $s.State; Say "service exe" $s.PathName
$nssm = (Get-Command nssm -ErrorAction SilentlyContinue).Source; Say "nssm on PATH" ($(if ($nssm) { $nssm } else { "no" }))
if ($nssm) { foreach ($k in "Application", "AppDirectory", "AppParameters", "ObjectName") { $v = (& $nssm get $Svc $k 2>$null) -join " "; Say ("nssm " + $k) ($v -replace "\0", "") } }
# env the service itself gets (names only, never values)
if ($nssm) { $e = (& $nssm get $Svc AppEnvironmentExtra 2>$null); Say "nssm extra env NAMES" ((($e -split "`n") | ForEach-Object { ($_ -split "=")[0] } | Where-Object { $_ }) -join ", ") }
# where git is, and whether that is on the MACHINE path (the service does not see user PATH)
$g = (Get-Command git -ErrorAction SilentlyContinue).Source; Say "git (this session)" ($(if ($g) { $g } else { "NOT FOUND" }))
$mp = [Environment]::GetEnvironmentVariable("Path", "Machine") -split ";" | Where-Object { $_ }
$gd = if ($g) { Split-Path $g } else { "" }
Say "git dir on MACHINE path" ($(if ($gd -and ($mp | Where-Object { $_.TrimEnd("\") -ieq $gd.TrimEnd("\") })) { "yes" } else { "NO (the service would not find git)" }))
Say "git version" ((& git --version) -join " ")
# ownership: git's "dubious ownership" rule compares the folder owner with the running user
$acl = Get-Acl $Root; Say "folder owner" $acl.Owner
Say "SYSTEM has access to folder" ($(if ($acl.Access | Where-Object { $_.IdentityReference -match "SYSTEM|Administrators" -and $_.FileSystemRights -match "FullControl|Modify|ReadAndExecute" }) { "yes" } else { "NOT SEEN" }))
# safe.directory: list entries per scope without changing anything
foreach ($sc in "--system", "--global") { $v = (& git config $sc --get-all safe.directory 2>$null) -join " ; "; Say ("safe.directory " + $sc.TrimStart("-")) ($(if ($v) { $v } else { "(none)" })) }
$sysProf = "C:\Windows\System32\config\systemprofile\.gitconfig"; Say "SYSTEM profile .gitconfig" ($(if (Test-Path $sysProf) { "exists" } else { "absent" }))
if (Test-Path $sysProf) { Say "  its safe.directory" (((& git config -f $sysProf --get-all safe.directory 2>$null)) -join " ; ") }
Write-Host ""
Write-Host "Reading: git trusts the folder for SYSTEM if the owner is SYSTEM/Administrators (and the service runs as such), or if safe.directory lists the folder (or *) in --system, or in the SYSTEM profile."
