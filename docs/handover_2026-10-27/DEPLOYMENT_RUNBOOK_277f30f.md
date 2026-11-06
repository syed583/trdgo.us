# TRDGO-US deployment runbook for release 277f30f (corrected 2026-10-27, NOT executed)

Release: branch `release/trdgo-us-safety-20261026`, commit **`277f30f849051471745f03b0b5401737fc5392d0`** (on the remote, verified).
Server today: `36094e89ff78c531bd6c8997208d31f02f1aafee` on `institutional-intelligence`, service `USStockReader` (NSSM, LocalSystem).
This file supersedes `FINAL_CUTOVER_RUNBOOK.md`, whose steps W1 and W4 still referred to the older commit `2776541` and to "two local items".
**Every step marked APPROVAL needs an explicit "go" in chat first. Nothing here changes production by itself.**

## 0. Where we are (verified on the VPS unless noted)
| Stage | Result |
|---|---|
| A0 push of `277f30f` | done, remote tip verified |
| Phase A read-only readiness | 33 PASS, 3 FAIL, 2 NOT VERIFIED (the FAILs were the dirty tree and the preflight copy) |
| B1a service / git facts | LocalSystem, git on the MACHINE path, folder owner BUILTIN\Administrators, no NSSM env overrides |
| B1b git as SYSTEM | PASS (a temporary scheduled task ran read-only git as SYSTEM, then was removed) |
| B2 stash of the five local files | done and verified; tree clean; stash holds the five files; backup copies in `C:\audit-reports\server-local-files-20261027-111430` |
| B3/B4 scripts installed with SHA-256 checks | preflight `481C3F75...74B7`, backup `465B19D1...E7EE`, update copy `F036371D...CB72` (all in `C:\audit-reports`) |
| B5 corrected preflight | NO-GO: fresh backup, 172 open paper positions, outside the window |
| Paper-position investigation | 172 = today's pre-market entries of six simulated strategies (opened 04:00 ET); no broker link; previous sessions closed normally by 16:00 ET |
| After-close check staged | see section 2 (this document's companion stage) |
Approvals already used: A0 (push), A1 (stash, as B2). **A2 (machine git settings) is NOT needed**: B1b passed.

## 1. Clocks and the window
* Entries are impossible by time Mon-Fri 20:00-04:00 ET and on weekends. The tools refuse to cut over outside that window.
* Until Sun 1 Nov: VPS = ET + 5 h, Dubai = ET + 8 h. So **16:05 ET = 21:05 VPS = 00:05 Dubai**, **20:00 ET = 01:00 VPS = 04:00 Dubai**.
  After 1 Nov: VPS = ET + 6 h, Dubai = ET + 9 h.
* The cut-over takes about 15-25 minutes after the dump. **Start the update no later than 02:00 ET** (07:00 VPS).

## 2. Paper-book gate (read-only; required before the dump)
The production build opens paper positions 04:00-20:00 ET, closes those entered before 16:00 ET at the first tick after 16:00
(`TIMED_EXIT`, a 60-second loop in the `demo-trade-tracker` thread), and **never closes a position entered 16:00-20:00 ET, nor
one left open from an earlier day**. So the book is only provably flat after 20:00 ET. Do not close or delete positions by hand.
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\audit-reports\phaseB_after_close_check.ps1
"exit code: $LASTEXITCODE"
```
| When | Command | Good result |
|---|---|---|
| 16:05 ET (21:05 VPS) | the command above | `VERDICT: WAIT`, exit 3, no `[FAIL]` line (positions opened before 16:00 are closed) |
| after 20:00 ET (01:00 VPS) | the command above | `VERDICT: PASS`, exit 0 |
Exit codes: **0 = PASS** (only after 20:00 ET or on a weekend), **1 = FAIL**, **3 = WAIT** (too early / not final), **2 = could not run**.
Anything other than 0 is NOT a GO. A FAIL means stop and report; the remedy (waiting, or a reviewed operator close with a fresh backup)
needs your approval and is never improvised.

## 3. Sequence inside the window
| # | Step | Needs | Success looks like |
|---|---|---|---|
| W0 | after-close check (section 2) | nothing | exit 0, `VERDICT: PASS` |
| W1 | corrected preflight via `phaseB_b5.ps1` (read-only) | nothing | every mandatory check PASS except **Fresh backup**; **no** Entry-window or Working-tree failure |
| W2 | **fresh dump** `take_predeploy_dump_277f30f.ps1` | **APPROVAL (A3)** | `DUMP VALIDATED`, SHA-256 printed |
| W3 | **fresh-backup gate** (section 4) | nothing | `GATE PASS` |
| W4 | corrected preflight again | nothing | the literal line `DECISION: GO` |
| W5 | point the branch at the release (git metadata only) | **APPROVAL (A4)** | `rev-parse "@{u}"` prints `277f30f849051471745f03b0b5401737fc5392d0` |
| W6 | `update_release_277f30f.ps1` = the DEPLOYMENT | **APPROVAL (A5)** | "Running with new entries LOCKED" |
| W7 | verify while LOCKED (section 6) | nothing | every item true |
| W8 | unlock | **separate APPROVAL (A6)** | `unlocked: true` |
| W9 | first-session watch | nothing | section 8 |

### W1 / W4 - reading the preflight correctly
The preflight prints `DECISION: GO` or `DECISION: NO-GO`, but **its process exit code is 0 in both cases** (the script has no `exit`;
proved by test). Read the decision LINE. Never gate on the exit code of the preflight or of `phaseB_b5.ps1`. `update.ps1` does not
call the VPS preflight; it gates on `release_tool.py preflight` (exit 0 only when ok, otherwise 1; a missing code also refuses).

### W5 - point the branch (A4)
```powershell
git -C C:\apps\us-stock-reader fetch --no-tags origin release/trdgo-us-safety-20261026
git -C C:\apps\us-stock-reader rev-parse FETCH_HEAD                       # must print 277f30f849051471745f03b0b5401737fc5392d0
git -C C:\apps\us-stock-reader branch --set-upstream-to=origin/release/trdgo-us-safety-20261026 institutional-intelligence
git -C C:\apps\us-stock-reader rev-parse "@{u}"                           # must print 277f30f849051471745f03b0b5401737fc5392d0
```

### W6 - the deployment (A5), elevated PowerShell
**Do NOT run `C:\apps\us-stock-reader\deploy\windows\update.ps1` from the current checkout** (old version: no preflight, no lock).
Run the verified release copy that B3/B4 installed (SHA-256 `F036371D3DE12F5D4442BCE5D95442DD2F85142DFBCFD295C4D221E6C2AACB72`):
```powershell
(Get-FileHash C:\audit-reports\update_release_277f30f.ps1 -Algorithm SHA256).Hash     # must equal the value above
powershell -ExecutionPolicy Bypass -File C:\audit-reports\update_release_277f30f.ps1 -Root C:\apps\us-stock-reader
```
Order: record the running commit; fetch; stage the target in a temp worktree and run `release_tool.py preflight` (window, paper
positions zero, every open signal recoverable, release check, clean tree) with the site's database settings for that one process;
`git pull --ff-only`; `pip install -r requirements.txt`; `npm ci` + `npm run build` (the live `frontend\dist` is rebuilt in place,
about 30 s of front-end glitch; copy `frontend\dist` first if you want a fallback); the backend test suite as a development process;
then `release_tool.py cutover` (preflight again, stop service, install the lock LOCKED, start, wait up to 60 s for `/safety`).
It never unlocks. It does **not** check that a dump exists: that is section 4.

## 4. FRESH-BACKUP GATE (explicit, before `update_release_277f30f.ps1`)
`update.ps1` has no backup check of its own. The only automatic backup gate is the VPS preflight's "Fresh backup" item (validated dump
no older than 60 minutes). Therefore, before W6, ALL of these must hold, and you read them yourself:
1. The dump was taken in this window, after the paper-book gate passed: `DUMP VALIDATED` with a SHA-256 and a `.sha256` file.
2. The gate below prints `GATE PASS` (newest `*_predeploy.dump`, at least 1 MB, SHA-256 equal to its `.sha256` file, at most **30** minutes
   old: the update takes 15-25 minutes before it stops the service, and the preflight limit is 60).
3. The preflight run after the dump printed the literal line `DECISION: GO`.
4. Record the dump file name, size, SHA-256 and time in the change log before starting.
If the gate fails or more than 30 minutes pass before W6, take a new dump (new approval) and repeat W3-W4.
```powershell
$d = Get-ChildItem C:\audit-reports\db-backups -Filter '*_predeploy.dump' | Sort-Object LastWriteTime | Select-Object -Last 1
$age = [math]::Round(((Get-Date) - $d.LastWriteTime).TotalMinutes)
$hash = (Get-FileHash $d.FullName -Algorithm SHA256).Hash
$ok = ($d.Length -ge 1MB) -and ($age -le 30) -and ((Get-Content ($d.FullName + '.sha256') -Raw) -match $hash)
($(if ($ok) { 'GATE PASS' } else { 'GATE FAIL' }) + ': ' + $d.Name + ', ' + [math]::Round($d.Length/1MB,1) + ' MB, ' + $age + ' min old')
```
The dump command (A3), only inside the window and after section 2 passed:
```powershell
if ((Get-FileHash C:\audit-reports\take_predeploy_dump_277f30f.ps1 -Algorithm SHA256).Hash -eq '465B19D1607A9B35E582AF4F52332EBDD6BD84F6DC81A6FBE2E674CED4D4E7EE') { powershell -NoProfile -ExecutionPolicy Bypass -File C:\audit-reports\take_predeploy_dump_277f30f.ps1 } else { Write-Host "HASH MISMATCH: not run" -ForegroundColor Red }
```
It is a PostgreSQL 17 `pg_dump -Fc` of schema `public` through the SESSION pooler (port 5432); read-only toward the database; credentials only in
process variables; never overwrites or deletes; a partial file is renamed `.failed`. Check the connection count in the preflight first (cap 15).
Data written by the old engine between the dump and the service stop (caches, snapshots) is not in the dump; keep that gap short.

## 5. If something stops
| Where it stops | State | What to do |
|---|---|---|
| preflight / release check | nothing changed | read the reason, fix, rerun |
| `git pull`, `pip`, `npm`, build, tests | service still runs the OLD code in memory; files may be new | fix and rerun the update copy (idempotent). Do not reboot in between |
| cutover: stop fails | service state unknown, database unchanged | `Get-Service USStockReader`; start nothing by hand. (The message says "Entries stay locked": at this stage nothing has been locked yet) |
| cutover: lock install fails | service left STOPPED (never started unlocked) | fix the database error, run `release_tool.py cutover` again |
| cutover: start fails | locked, not running | read `service.err.log`; do not unlock |
| `/safety` missing / unsafe / wrong commit | running, locked | section 6; do not unlock |
| `/safety` answers but `commit` is empty | running, locked | git / service-account issue (B1b showed SYSTEM git works, so unexpected) |
Rollback: there is no automatic rollback to `36094e8`, `683fce1` or `main` (they fail `release_check.py`). Order of preference: fix forward behind
the lock; return to the last passing release commit; break-glass with your approval each time (old build behind the database lock, only while
open paper positions are zero, `entry_lock.py status` first). The dump is for a disaster and is restored into a scratch database first, never over live data without approval.

## 6. Verification while LOCKED (W7)
```powershell
(Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing).Content
(Invoke-WebRequest http://127.0.0.1:8000/safety -UseBasicParsing).Content
cd C:\apps\us-stock-reader\backend
.\.venv\Scripts\python.exe entry_lock.py status
.\.venv\Scripts\python.exe intraday_signal_v2.py stale
git -C C:\apps\us-stock-reader status --porcelain          # prints nothing (the five files stay stashed: do NOT pop them)
git -C C:\apps\us-stock-reader rev-parse HEAD              # 277f30f849051471745f03b0b5401737fc5392d0
```
`/safety` must show `confirm15_required` true, `can_be_disabled` false, `static_checks` true, `unsafe_build` null, `running_matches_tree` true,
`uncommitted_changes` false, `entry_locked` true, `commit` starting **`277f30f8`**, `database_dialect` postgresql, `environment` production.
HTML instead of JSON means the OLD build is still running.

## 7. Unlock (A6, separate approval)
```powershell
cd C:\apps\us-stock-reader\backend
.\.venv\Scripts\python.exe release_tool.py unlock --url http://127.0.0.1:8000 --expect-commit 277f30f849051471745f03b0b5401737fc5392d0
```
It re-asks the RUNNING service and refuses unless every item of section 6 holds. Unlocking only makes new entries POSSIBLE; each still needs a
confirmed closed 15-minute reading. The paper tracker of the new build may open from 04:00 ET and the live engine from 10:00 ET.

## 8. After unlock
Boards show WAIT where data is stale or conflicting; `/api/uw/health` OK without `concurrent_limit_exceeded` bursts; recovered live signals close by
their rules, once each; stale rows stay open and reported (retiring them is a separate approval); stop the scratch restore instance on port 5433
if it runs; old `.env.bak` / `.env.seoul_*` untouched (removal is a separate approval).

## 9. Approvals (none assumed)
A0 push (done) | A1 stash (done as B2) | A2 machine git settings (not needed) | **A3 run the backup** | **A4 point the branch** | **A5 run the update copy = deploy** |
**A6 unlock** | A7 expire stale signals (optional) | A8 break-glass rollback (only if needed) | A9 delete `.env` backups (optional, later) |
A10 reviewed operator close of paper positions (ONLY if section 2 fails after 20:00 ET; never improvised).

## 10. Open items before the window
* **`INTRADAY_MODEL` appears more than once in `backend\.env`** (B3/B4 review). Confirm the effective value (the LAST line wins; a machine variable
  would override) is the intended one; the new build reads it as v2 only when it is exactly `v2`.
* The paper book must reach zero at 20:00 ET (section 2). After-hours entries (16:00-20:00 ET) are the only way it will not.
* Re-run the corrected preflight at W1: Entry window and Fresh backup are the only items expected to differ from today's NO-GO, plus the paper count.
* Not rehearsable off the VPS: `Stop-Service` / `Start-Service` of `USStockReader`; the real pooler; real Unusual Whales traffic after the start.
