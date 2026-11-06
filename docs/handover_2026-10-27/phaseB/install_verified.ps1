param([Parameter(Mandatory = $true)][ValidateSet("preflight", "backup", "update")][string]$Name,
      [string]$Root = "C:\apps\us-stock-reader", [string]$ReportDir = "C:\audit-reports",
      [string]$Commit = "277f30f849051471745f03b0b5401737fc5392d0",
      [string]$Branch = "release/trdgo-us-safety-20261026",
      [switch]$NoFetch)
# TRDGO-US Phase B / stages B3-B4: install ONE script from the verified release commit, byte-exact, with SHA-256 validation.
# Writes only into $ReportDir (a new file, or the old one renamed, never deleted). Never executes the installed script.
# The only git change is `git fetch` of the release branch: it ADDS objects and a remote ref, and touches no working file.
$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
# what the approved release commit contains (computed from the commit itself, independently of this server)
$spec = @{
    preflight = @{ Path = "deploy/windows/vps_predeploy_preflight.ps1"; Blob = "52706eff41d5b23a363c9b71c3ba355faf2bce3e"; Sha = "EDD3FF988B3F6DE9692B86E6C3D2AE5D487ECA3509C426BDBC0447DB23D88181"
                   Dest = "vps_predeploy_preflight_277f30f.ps1"; Placeholder = "REPLACE_WITH_THE_APPROVED_RELEASE_SHA"; FinalSha = "481C3F7507F2D68204472380EC57D28719B13519BCA07D92E5B802243F4674B7" }
    backup    = @{ Path = "deploy/windows/take_predeploy_dump.ps1"; Blob = "43136cef867594a72671ec76788c4b5c62689111"; Sha = "465B19D1607A9B35E582AF4F52332EBDD6BD84F6DC81A6FBE2E674CED4D4E7EE"
                   Dest = "take_predeploy_dump_277f30f.ps1" }
    update    = @{ Path = "deploy/windows/update.ps1"; Blob = "bd2526c65ff69c4a8d71796267dbe6801e81982b"; Sha = "F036371D3DE12F5D4442BCE5D95442DD2F85142DFBCFD295C4D221E6C2AACB72"
                   Dest = "update_release_277f30f.ps1" }
}[$Name]
function Fail([string]$why) { Write-Host ("[FAIL] " + $why) -ForegroundColor Red; Write-Host "NOTHING WAS INSTALLED." -ForegroundColor Red; exit 2 }
function Ok([string]$what) { Write-Host ("[PASS] " + $what) }
function Git-Text { param([Parameter(ValueFromRemainingArguments = $true)]$a) $o = & git -C $Root @a 2>$null; return (("" + ($o -join "`n")).Trim()) }
function Git-Bytes([string[]]$a) {                      # binary-safe stdout
    $psi = New-Object System.Diagnostics.ProcessStartInfo; $psi.FileName = "git"; $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.CreateNoWindow = $true
    $psi.Arguments = (@("-C", ('"' + $Root + '"')) + $a) -join " "
    $p = [System.Diagnostics.Process]::Start($psi); $ms = New-Object System.IO.MemoryStream
    $p.StandardOutput.BaseStream.CopyTo($ms); $null = $p.StandardError.ReadToEnd(); $p.WaitForExit()
    if ($p.ExitCode -ne 0) { return $null }; return $ms.ToArray()
}
function Sha-Bytes([byte[]]$b) { $h = [System.Security.Cryptography.SHA256]::Create(); (($h.ComputeHash($b) | ForEach-Object { $_.ToString("X2") }) -join "") }

# ---- 1. the commit is really the approved one -----------------------------------------------------------------------------
if (-not $NoFetch) {
    $null = & git -C $Root fetch origin $Branch 2>&1
    if ($LASTEXITCODE -ne 0) { Fail "git fetch of the release branch failed (network or credentials)" }
    $fh = Git-Text rev-parse FETCH_HEAD
    if ($fh -ne $Commit) { Fail ("the remote branch is now " + $fh + ", not the approved " + $Commit + " (it moved: do not use it)") }
    Ok ("fetched; the remote release branch is exactly " + $Commit)
}
if ((Git-Text cat-file -t $Commit) -ne "commit") { Fail ("commit " + $Commit + " is not in this repository (run without -NoFetch)") }
Ok "the approved commit is present in the repository"

# ---- 2. the file in that commit is the expected one --------------------------------------------------------------------------
$blob = Git-Text rev-parse ($Commit + ":" + $spec.Path)
if ($blob -ne $spec.Blob) { Fail ("the git object of " + $spec.Path + " in that commit is " + $blob + ", expected " + $spec.Blob) }
Ok ("git object id of " + $spec.Path + " = " + $blob)
$bytes = Git-Bytes @("cat-file", "blob", $blob)
if ($null -eq $bytes -or $bytes.Length -lt 100) { Fail "could not read the file's bytes from git" }
$sha = Sha-Bytes $bytes
if ($sha -ne $spec.Sha) { Fail ("SHA-256 of the committed bytes is " + $sha + ", expected " + $spec.Sha) }
Ok ("SHA-256 of the committed bytes = " + $sha + " (" + $bytes.Length + " bytes)")
if (@($bytes | Where-Object { $_ -gt 127 }).Count -gt 0) { Fail "the file contains non-ASCII bytes (unexpected)" }

# ---- 3. the content to install (the preflight needs the release SHA written in, byte-exact; nothing else changes) ----------------
$final = $bytes
if ($spec.Placeholder) {
    $txt = [System.Text.Encoding]::ASCII.GetString($bytes)
    $n = ([regex]::Matches($txt, [regex]::Escape($spec.Placeholder))).Count
    if ($n -ne 1) { Fail ("expected exactly one placeholder, found " + $n) }
    $final = [System.Text.Encoding]::ASCII.GetBytes($txt.Replace($spec.Placeholder, $Commit))
    $fs = Sha-Bytes $final
    if ($fs -ne $spec.FinalSha) { Fail ("SHA-256 after writing the release SHA in is " + $fs + ", expected " + $spec.FinalSha) }
    Ok ("release SHA written into the single placeholder; SHA-256 of the result = " + $fs)
}
# syntax only: the file is parsed, never run
$tmp = Join-Path $env:TEMP ("verify_" + $stamp + ".ps1"); [System.IO.File]::WriteAllBytes($tmp, $final)
$tk = $null; $er = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($tmp, [ref]$tk, [ref]$er); Remove-Item $tmp -ErrorAction SilentlyContinue
if ($er.Count -gt 0) { Fail ("the file has " + $er.Count + " syntax error(s)") }
Ok "the file parses (it was not executed)"

# ---- 4. install: a new file; an existing different one is renamed, never deleted ---------------------------------------------------
$null = New-Item -ItemType Directory -Force -Path $ReportDir
$dest = Join-Path $ReportDir $spec.Dest
if (Test-Path $dest) {
    $have = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
    if ($have -eq (Sha-Bytes $final)) { Ok ("already installed and identical: " + $dest); Write-Host "DONE (nothing to change)."; exit 0 }
    $old = $dest + ".old-" + $stamp; Rename-Item -LiteralPath $dest -NewName (Split-Path $old -Leaf)
    Write-Host ("an existing different file was renamed (kept): " + $old)
}
[System.IO.File]::WriteAllBytes($dest, $final)
$back = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
if ($back -ne (Sha-Bytes $final)) { Fail "the installed file does not read back identical" }
Ok ("installed: " + $dest + "  SHA-256 " + $back)
Write-Host ""
Write-Host "INSTALLED AND VERIFIED. Not executed. Reference for later:" -ForegroundColor Green
Write-Host ("  " + $dest + "   SHA-256 " + $back)
