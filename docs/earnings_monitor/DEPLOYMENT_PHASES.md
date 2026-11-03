# Earnings Intelligence: deployment phases (nothing here has been run on the server)

Every phase needs your explicit approval. Verification commands and rollback commands are in separate blocks on purpose; a rollback is never part of a routine check.
Run in an elevated PowerShell on the VPS. Use the variable names below (they are new, so they cannot collide with variables left in an old session).

## Phase 0: find out what is really installed (read only)
The owner reported that the A.2 update was rolled back to `_backup_monitor_update_20261103_161524_937` after it was verified. That backup holds the code from BEFORE the A.2 update (Stage A, `d48f4900`).
```powershell
$p0Dir = "C:\apps\_deploy_probe_$(Get-Date -Format yyyyMMdd_HHmmss)"; New-Item -ItemType Directory -Path $p0Dir | Out-Null
foreach ($p0 in @(@('a2','499d57326b5dd1e24328597b25aa3b814efea822'), @('a4','<COMMIT OF THE APPROVED A.4 PUSH>'))) { }   # see Phase 1 for A.4; A.2 archive:
Invoke-WebRequest "https://github.com/syed583/trdgo.us/archive/499d57326b5dd1e24328597b25aa3b814efea822.zip" -OutFile "$p0Dir\a2.zip" -UseBasicParsing
Expand-Archive "$p0Dir\a2.zip" "$p0Dir\a2"
$p0A2 = (Get-ChildItem "$p0Dir\a2")[0].FullName + '\deploy\earnings_monitor'
& "$p0A2\identify_installed_version.ps1" -Packages "A2=$p0A2" 2>&1
```
(The A.2 archive does not contain `identify_installed_version.ps1`; copy it from the A.4 package once A.4 is downloaded, or use the one-line check below.) One-line check without any script: `(Get-FileHash C:\apps\us-stock-reader\backend\earnings_monitor\routes.py).Hash` and compare with `routes.py` in each release's `MANIFEST.sha256`.

## Phase 1: Stage A.4 (monitor + dashboard, cache-only), after approval of the push
Release `release/stage-a4`, manifest SHA-256 `d32212fe521d768a6d5688f5ba89606ae2326fe667abfb60a57e746c15675a7f`, 134 files. The Update action works from Stage A or from A.2 (it replaces monitor files and the frontend; `main.py` and `.env` are untouched).
```powershell
$a4Commit = '<the commit hash I give you after the push>'
$a4Anchor = 'd32212fe521d768a6d5688f5ba89606ae2326fe667abfb60a57e746c15675a7f'
$a4Dir = "C:\apps\_deploy_a4_$(Get-Date -Format yyyyMMdd_HHmmss)"; New-Item -ItemType Directory -Path $a4Dir | Out-Null
Invoke-WebRequest "https://github.com/syed583/trdgo.us/archive/$a4Commit.zip" -OutFile "$a4Dir\a4.zip" -UseBasicParsing
Expand-Archive "$a4Dir\a4.zip" "$a4Dir\x"
$a4Pkg = (Get-ChildItem "$a4Dir\x")[0].FullName + '\deploy\earnings_monitor'
$a4Hash = (Get-FileHash "$a4Pkg\MANIFEST.sha256").Hash.ToLower(); "manifest $a4Hash"; if ($a4Hash -ne $a4Anchor) { throw 'STOP: manifest hash mismatch' }
cd $a4Pkg
.\identify_installed_version.ps1 -Packages "A4=$a4Pkg"                                  # what is installed now (read only)
.\verify_after_deploy.ps1 2>&1 | Tee-Object "$a4Dir\verify_before.txt"                  # baseline (read only)
```
Do NOT run `-Action Preflight` on an installed monitor (it is for a first install and stops with "already contains the monitor patch"). The Update below runs its own preflight.
```powershell
.\deploy_earnings_monitor.ps1 -Action Update -Package $PWD.Path -ManifestSha256 $a4Anchor
```
Expected: `manifest anchor OK`, `package OK: 134 files`, `health (baseline) OK`, `backup written: C:\apps\us-stock-reader\_backup_monitor_update_<timestamp>`, `UPDATE OK`, and `monitor still reports scheduling CACHE_ONLY`. It rolls back by itself on any failure. Note the printed backup path.

Verification (read only; run after the update and again after the next scan at :00 or :30):
```powershell
.\verify_after_deploy.ps1 2>&1 | Tee-Object "$a4Dir\verify_after_$(Get-Date -Format HHmmss).txt"
```
Stop conditions: any FAIL line, `provider requests today` above 0, a scheduler mode other than CACHE_ONLY, or a failed Options Flow / Engine20 check. Then stop and send me the output; do not roll back unless I say so.

Rollback (ONLY if told to; uses the backup the Update printed, which is also the newest folder):
```powershell
$rbDir = (Get-ChildItem C:\apps\us-stock-reader -Directory -Filter '_backup_monitor_update_*' | Sort-Object LastWriteTime | Select-Object -Last 1).FullName; "rolling back from $rbDir"
.\deploy_earnings_monitor.ps1 -Action Rollback -Package $PWD.Path -BackupDir $rbDir
```
Older backups are never deleted by any script here.

## Phase 2: Stage B1 (shared Unusual Whales slots, installed INERT)
Branch `release/uw-concurrency` commit `194ad28e2a58390a9ed9f39e32da6e3090d04288`, manifest SHA-256 `1c12a93b6d338c42e9ac39134483133b245c355f867928e19d8bdef17f334a4e`. It replaces `backend\unusualwhales_service.py` and adds three modules; nothing changes until environment switches are set.
```powershell
cd <package folder>\deploy\uw_concurrency
.\install_uw_concurrency.ps1 -Action Preflight -Package $PWD.Path -ManifestSha256 1c12a93b6d338c42e9ac39134483133b245c355f867928e19d8bdef17f334a4e
.\install_uw_concurrency.ps1 -Action Install   -Package $PWD.Path -ManifestSha256 1c12a93b6d338c42e9ac39134483133b245c355f867928e19d8bdef17f334a4e -Restart
```
B2 (activate): the same Install with `-ConfigureSlots`, only after the topology is confirmed (one machine = machine slots only; a second machine sharing the token additionally needs the lease broker, which is NOT mounted and NOT tested across real machines).

## Phase 3: Stage C (private Smart Money Move engine kit)
Private kit (never on GitHub), manifest SHA-256 `db40e2aa37ce7a822280335f6312c7eb96a33393b1e63e83212b24f95e5e1dcc`; installer commit `d4320db7f381bf615f3c79f53ff0cf16a4314816`. Installs code only, restarts nothing, collects nothing.
```powershell
.\install_smart_move_engine.ps1 -Action Preflight -Package $PWD.Path -ManifestSha256 db40e2aa37ce7a822280335f6312c7eb96a33393b1e63e83212b24f95e5e1dcc
.\install_smart_move_engine.ps1 -Action Install   -Package $PWD.Path -ManifestSha256 db40e2aa37ce7a822280335f6312c7eb96a33393b1e63e83212b24f95e5e1dcc
```

## Phase 4: Stage D (collection), separate approvals, in this order
1. Dry run of the scheduled wrapper (zero requests): `run_engine20_collection.ps1 -Runner ... -StoreDir ... -StateRoot ... -SelectPolicy deadline -SelectTop 400` (no `-Live`).
2. Register the hourly task with `register_collection_task.ps1 -Create` (dry-run arguments).
3. Go live: a signed attestation file, `SMART_MOVE_COLLECTION_LIVE=1` set by you, the wrapper started with `-Live -Attestation <file>`; budget per the table in `SMART_MOVE_COLLECTION_POLICY.md` and written provider confirmation first.

## Phase 5: Stage E external alerts / WebSocket: not enabled; alerts go to the local outbox only.
