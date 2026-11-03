<#
READ-ONLY. Tells you which release of the earnings monitor is actually installed, by comparing the installed files with the MANIFEST.sha256 of each release package you point it at. It changes nothing, restarts nothing and sends nothing.

  .\identify_installed_version.ps1 -Packages 'C:\deploy\x\...\deploy\earnings_monitor','C:\apps\_deploy_a2_...\deploy\earnings_monitor'      (any number of extracted packages; give each a label with Label=Path if you like)

For every package it prints how many of its monitor code files and frontend files are IDENTICAL to what is installed. A release is "INSTALLED" only if every file matches.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string[]]$Packages,
  [string]$App = 'C:\apps\us-stock-reader'
)
$ErrorActionPreference = 'Stop'
$Packages = @($Packages | ForEach-Object { $_ -split ',' } | Where-Object { $_.Trim() })          # powershell -File passes one comma-joined string
function Rel-To-Installed([string]$rel) {
  $r = $rel.Replace('/', '\')
  if ($r.StartsWith('backend\')) { return Join-Path $App $r }
  if ($r.StartsWith('frontend_dist\')) { return Join-Path $App ('frontend\dist\' + $r.Substring('frontend_dist\'.Length)) }
  return $null
}
foreach ($spec in $Packages) {
  $label = $spec; $path = $spec
  if ($spec -match '^([^\\:=]+)=(.+)$') { $label = $Matches[1]; $path = $Matches[2] }
  $man = Join-Path $path 'MANIFEST.sha256'
  if (-not (Test-Path -LiteralPath $man)) { Write-Host ("{0}: no MANIFEST.sha256 at {1}" -f $label, $path); continue }
  $same = 0; $diff = 0; $missing = 0; $differing = @()
  foreach ($l in Get-Content -LiteralPath $man) {
    if ($l -notmatch '^([0-9a-fA-F]{64})\s+\*?(.+)$') { continue }
    $inst = Rel-To-Installed $Matches[2].Trim()
    if (-not $inst) { continue }                                   # deployment scripts and docs are not installed files
    if (-not (Test-Path -LiteralPath $inst)) { $missing++; $differing += $Matches[2]; continue }
    if ((Get-FileHash -LiteralPath $inst -Algorithm SHA256).Hash.ToLower() -eq $Matches[1].ToLower()) { $same++ } else { $diff++; $differing += $Matches[2] }
  }
  $verdict = if ($diff -eq 0 -and $missing -eq 0 -and $same -gt 0) { 'INSTALLED (every file identical)' } else { 'not installed as a whole' }
  Write-Host ("{0}: manifest sha256 {1}" -f $label, (Get-FileHash -LiteralPath $man -Algorithm SHA256).Hash.ToLower())
  Write-Host ("    identical={0} different={1} missing={2}  ->  {3}" -f $same, $diff, $missing, $verdict)
  if ($differing.Count -and $differing.Count -le 12) { $differing | ForEach-Object { Write-Host ("      differs: {0}" -f $_) } }
}
Write-Host ''
Write-Host 'Backups present (a rollback restores the state recorded in the backup, i.e. the code from BEFORE the update that made it):'
Get-ChildItem -LiteralPath $App -Directory -Filter '_backup_*' | Sort-Object Name | ForEach-Object { Write-Host ("  {0}" -f $_.Name) }
