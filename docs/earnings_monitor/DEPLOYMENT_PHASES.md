# Earnings Intelligence: deployment phases (nothing here has been run on the server)

Every phase needs your explicit approval. Verification blocks and rollback blocks are separate on purpose: a rollback is never part of a routine check, and no command below picks a backup by guessing ("newest folder"). Each install records its own backup path and release identity (`monitor_install_record.json`, Phase 1; the Stage C/D installers keep their own records), and a rollback uses only that record.

Run in an elevated PowerShell on the VPS. Variable names are new on purpose (an old session may still hold variables such as `$b`).
Public code lives in `syed583/trdgo.us` (public). The Stage C kit and the Stage D collector are PRIVATE: they are never in this repository or its archives; they are delivered to you as zip files with a manifest hash.

## Phase 0: find out what is really installed (read only)
```powershell
$p0Commit = 'a2826be2155ebf52da935ea7ca1145c30a055b2f'     # the A.4 release below; this phase only DOWNLOADS it, nothing is installed
$p0Dir = "C:\apps\_deploy_probe_$(Get-Date -Format yyyyMMdd_HHmmss)"; New-Item -ItemType Directory -Path $p0Dir | Out-Null
Invoke-WebRequest "https://github.com/syed583/trdgo.us/archive/$p0Commit.zip" -OutFile "$p0Dir\a4.zip" -UseBasicParsing
Expand-Archive "$p0Dir\a4.zip" "$p0Dir\x"
$p0Pkg = (Get-ChildItem "$p0Dir\x")[0].FullName + '\deploy\earnings_monitor'
if ((Get-FileHash "$p0Pkg\MANIFEST.sha256").Hash.ToLower() -ne '29e3e292d83b96c39fe76d7f80f4f42cc92beda055bce0bd0c01d7ab857e5a90') { throw 'STOP: manifest mismatch' }
& "$p0Pkg\identify_installed_version.ps1" -Packages "A4=$p0Pkg"
```
It prints, per release, how many installed files are identical and "INSTALLED (every file identical)" for the one that is. To compare with Stage A or A.2 as well, download their archives the same way (commits `d48f4900a8a66d5b2572ac5c68536d72319d02d5` and `499d57326b5dd1e24328597b25aa3b814efea822`) and add them to `-Packages` as `StageA=<path>,A2=<path>`. Tested against Stage A, A.2 and A.4 installations: each was recognised and the others were not. Reads files only.

## Phase 1: Stage A.4 (monitor + dashboard, cache-only)
Branch `release/stage-a4`, commit `a2826be2155ebf52da935ea7ca1145c30a055b2f`, manifest SHA-256 `29e3e292d83b96c39fe76d7f80f4f42cc92beda055bce0bd0c01d7ab857e5a90`, 134 files. The Update works from Stage A or from A.2 (it replaces monitor files and the frontend; `main.py` and `.env` are untouched).
```powershell
$a4Commit = 'a2826be2155ebf52da935ea7ca1145c30a055b2f'
$a4Anchor = '29e3e292d83b96c39fe76d7f80f4f42cc92beda055bce0bd0c01d7ab857e5a90'
$a4Dir = "C:\apps\_deploy_a4_$(Get-Date -Format yyyyMMdd_HHmmss)"; New-Item -ItemType Directory -Path $a4Dir | Out-Null
Invoke-WebRequest "https://github.com/syed583/trdgo.us/archive/$a4Commit.zip" -OutFile "$a4Dir\a4.zip" -UseBasicParsing
Expand-Archive "$a4Dir\a4.zip" "$a4Dir\x"
$a4Pkg = (Get-ChildItem "$a4Dir\x")[0].FullName + '\deploy\earnings_monitor'
$a4Hash = (Get-FileHash "$a4Pkg\MANIFEST.sha256").Hash.ToLower(); "manifest $a4Hash"; if ($a4Hash -ne $a4Anchor) { throw 'STOP: manifest hash mismatch' }
cd $a4Pkg
.\identify_installed_version.ps1 -Packages "A4=$a4Pkg"                                  # what is installed now (read only)
.\verify_after_deploy.ps1 2>&1 | Tee-Object "$a4Dir\verify_before.txt"                  # baseline (read only)
```
Do NOT run `-Action Preflight` on an installed monitor (it is for a first install and stops with "already contains the monitor patch"). The Update runs its own preflight.
```powershell
.\deploy_earnings_monitor.ps1 -Action Update -Package $PWD.Path -ManifestSha256 $a4Anchor
```
Expected: `manifest anchor OK`, `package OK: 134 files`, `health (baseline) OK`, `backup written: C:\apps\us-stock-reader\_backup_monitor_update_<timestamp>`, `UPDATE OK`, `install recorded in C:\apps\us-stock-reader\monitor_install_record.json`, `monitor still reports scheduling CACHE_ONLY`. It rolls back by itself on any failure.

Verification (read only; right after, and again after the next scan at :00 or :30):
```powershell
.\verify_after_deploy.ps1 2>&1 | Tee-Object "$a4Dir\verify_after_$(Get-Date -Format HHmmss).txt"
```
Stop conditions: any FAIL line, `provider requests today` above 0, a scheduler mode other than CACHE_ONLY, a failed Options Flow / Engine20 check. Stop and send me the output. Do not roll back unless I say so.

Rollback (ONLY if told to). It takes no path: it rolls back the install recorded in `monitor_install_record.json`, after checking that the recorded backup is intact (hash of every file) and that the installed files are exactly what that install left. Otherwise it stops and changes nothing. Older backups are never touched or deleted.
```powershell
.\deploy_earnings_monitor.ps1 -Action Rollback -Package $PWD.Path
```
A backup that predates the record (for example `_backup_monitor_update_20261103_161524_937`) is used only with an explicit `-BackupDir <that folder> -AcceptUnrecordedBackup`; that case is not covered by the integrity check.

## Phase 2: Stage B1 (shared Unusual Whales slots, installed INERT)
Branch `release/uw-concurrency`, commit `194ad28e2a58390a9ed9f39e32da6e3090d04288`, manifest SHA-256 `1c12a93b6d338c42e9ac39134483133b245c355f867928e19d8bdef17f334a4e`. Replaces `backend\unusualwhales_service.py` (only if it is exactly the known version) and adds three modules; inert until environment switches are set. Download it exactly as in Phase 1 into a new folder `$bDir`, then:
```powershell
cd "$((Get-ChildItem "$bDir\x")[0].FullName)\deploy\uw_concurrency"
.\install_uw_concurrency.ps1 -Action Preflight -Package $PWD.Path -ManifestSha256 1c12a93b6d338c42e9ac39134483133b245c355f867928e19d8bdef17f334a4e
.\install_uw_concurrency.ps1 -Action Install   -Package $PWD.Path -ManifestSha256 1c12a93b6d338c42e9ac39134483133b245c355f867928e19d8bdef17f334a4e -Restart
```
Rollback (only if told to): `.\install_uw_concurrency.ps1 -Action Rollback -Package $PWD.Path -BackupDir <the backup folder the Install printed>` (this installer restores that backup byte for byte and removes only the files it added).
B2 (activate) is the same Install with `-ConfigureSlots`, only after the topology is confirmed (one machine = machine slots only; a second machine sharing the token needs the lease broker, which is NOT mounted and was only tested with processes on one computer).

## Phase 3: Stage C (private Smart Money Move engine kit v2; code only, nothing collects)
Private zip `stage_c_kit_v2.zip` (SHA-256 `90fcd25a57ca4341712c3b9b1a1ad0de1fd0ebb930a2ab623277923b6d148f79`), manifest SHA-256 `7a4b52a1cea9d4c6828e39fe5f10964f733b00f326df7a169799fb58dfe915c9`, source commit `ad0d63c930ca62f30d6849a63d08f23f8ed94f0a`. Scoring files are byte-identical to kit v1 (scorer, freshness, cutoff, calendar, prereg); only selection, the optional 429 policy and the evidence reader changed.
```powershell
cd <where you extracted stage_c_kit_v2>
.\install_smart_move_engine.ps1 -Action Preflight -Package $PWD.Path -ManifestSha256 7a4b52a1cea9d4c6828e39fe5f10964f733b00f326df7a169799fb58dfe915c9
.\install_smart_move_engine.ps1 -Action Install   -Package $PWD.Path -ManifestSha256 7a4b52a1cea9d4c6828e39fe5f10964f733b00f326df7a169799fb58dfe915c9
```
Rollback (only if told to): `.\install_smart_move_engine.ps1 -Action Rollback -Package $PWD.Path` removes only the files its own install record lists (and only if still identical). The evidence store folder is never touched.
Evidence store: `C:\apps\us-stock-reader\sm_evidence_store` on the VPS (installer default; `SMART_MOVE_EVIDENCE_STORE` and `SMART_MOVE_ENGINE_ROOT` are written to `.env`). The collector writes it and the monitor's engine reads the same folder: one store, no second database.

## Phase 4: Stage D collector files + scheduler, DRY RUN only
Private zip `stage_d_collector.zip` (SHA-256 `dde44859cbcd0e714a7355c6851845511517ce880c48fe3a6348dd90f9b9d4f3`), manifest SHA-256 `1bf3b22e4644d26857c22be335ceaebc0b6783a7fe8dfd8497e7270e05c64a9e`. Requires Phase 3. Its preflight refuses (and changes nothing) if Stage C is missing or if any application module the collector imports (77 listed) is absent.
```powershell
cd <where you extracted stage_d_collector>
.\install_smart_move_collector.ps1 -Action Preflight -Package $PWD.Path -ManifestSha256 1bf3b22e4644d26857c22be335ceaebc0b6783a7fe8dfd8497e7270e05c64a9e
.\install_smart_move_collector.ps1 -Action Install   -Package $PWD.Path -ManifestSha256 1bf3b22e4644d26857c22be335ceaebc0b6783a7fe8dfd8497e7270e05c64a9e
Copy-Item C:\apps\us-stock-reader\collector_tools\collector.settings.template.txt C:\apps\us-stock-reader\collector_tools\collector.settings.txt     # Mode=DRY_RUN in the template
cd C:\apps\us-stock-reader\collector_tools
.\register_collection_task.ps1 -Create                                       # hourly, 24 h, every day, plus at boot; runs as SYSTEM; DRY RUN because the settings say so
.\verify_collector.ps1 -StateRoot C:\apps\us-stock-reader\sm_collector_state       # read only
```
Rollback of the task (only if told to): `.\register_collection_task.ps1 -Remove`. Rollback of the files: `.\install_smart_move_collector.ps1 -Action Rollback -Package <the extracted stage_d_collector folder>`.
The monitor reads the collector's log for its alerts when `EM_COLLECTOR_LOG=C:\apps\us-stock-reader\sm_collector_state\collection_runs.jsonl` is set in `.env` (a separate approved change; without it those alerts stay silent).

## Phase 5: small controlled live test (separate approval, written provider confirmation first)
In `collector.settings.txt`: `Mode=LIVE`, `Attestation=<the file you sign>`, defaults `Authorize=50`, `DailyLimit=100`; set the machine variable `SMART_MOVE_COLLECTION_LIVE=1` yourself. One hour's run, then `verify_collector.ps1` and a look at the dashboard.

## Phase 6: full automatic predictions (separate approval)
Recommended after the test: `SelectPolicy=deadline`, `SelectTop=400`, `Authorize=150`, `DailyLimit=400`, with `SMART_MOVE_RUN_MAX=150` and `SMART_MOVE_DAILY_MAX=400` set in the machine environment (the runner's built-in ceilings stay 50 / 100 until you set them), 24-hour schedule (the template default). Optional `SMART_MOVE_429_POLICY=backoff`.

## Phase 7: B2 activation and external alerts: only when you ask. External alerts and WebSocket are not enabled by anything here; alerts go to the local outbox only.
