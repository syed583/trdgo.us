# TRDGO-US final cut-over runbook (prepared 2026-10-27, NOT executed)

Release: branch `release/trdgo-us-safety-20261026`, commit **`277f30f849051471745f03b0b5401737fc5392d0`**. Server today: `36094e89ff78c531bd6c8997208d31f02f1aafee` on `institutional-intelligence`.
Nothing in this document has been run on the VPS. **Every step marked APPROVAL needs your explicit "go" in chat first.**

**Which commit.** The commit you approved and that is on the remote is `2776541...` (`27765413ec899817e06690c40dc3122488468782`).
Rehearsing the deployment in a clean checkout found that `update.ps1` would have STOPPED at its test step on the real server
(the site's `.env` says `APP_ENV=production` and the server has no `.env.dev`, so 5 development-isolation tests fail). The fix is
in `update.ps1` only (it runs the suite as a development process, with variables that exist for that one process), plus two
new deploy scripts and one test. It is the local commit `277f30f849051471745f03b0b5401737fc5392d0`, which differs from the approved one in
exactly four files: `deploy/windows/update.ps1`, `deploy/windows/vps_predeploy_preflight.ps1`,
`deploy/windows/take_predeploy_dump.ps1` and `backend/tests/test_release_workflow.py`. **No application code differs.** It is NOT
pushed. **A0 (approval): push `277f30f` to `origin/release/trdgo-us-safety-20261026` (fast-forward).** Everything below uses `277f30f`;
the preflight refuses until it is on the remote. If you prefer to keep `2776541`, the deployment will stop at the tests step
unless the five tests are dealt with some other way (not recommended).

## 0. Clocks and the window
* Entries are impossible by time Mon-Fri 20:00-04:00 ET and all weekend. The tools refuse to cut over outside that window.
* Until 1 Nov: VPS clock = ET + 5 h, Dubai = ET + 8 h. After 1 Nov (US clocks go back): VPS = ET + 6 h, Dubai = ET + 9 h.
  So 20:00 ET = **01:00 VPS = 04:00 Dubai**; 04:00 ET = 09:00 VPS = 12:00 Dubai (until 1 Nov).
* The cut-over itself takes about 15-25 minutes (dump 5-10, preflight 2, update.ps1 10-15, checks 5). **Start no later than
  02:00 ET** so it is finished well before the paper tracker may open entries at 04:00 ET.

## 1. Read-only checks BEFORE the window (any time; they change nothing)
These catch the things that would stall the cut-over. All are read-only.

1. **The service account and git.** The running site reports its own commit by running `git` from the service process; if git is
   not on the service's PATH, or refuses the folder ("dubious ownership"), `/safety` shows no commit and the unlock is refused
   (entries stay locked: safe, but the cut-over cannot finish).
   ```powershell
   (Get-CimInstance Win32_Service -Filter "Name='USStockReader'") | Select-Object Name, StartName, State
   (Get-Acl C:\apps\us-stock-reader).Owner
   git config --system --get-all safe.directory
   ([Environment]::GetEnvironmentVariable("Path","Machine") -split ';') | Where-Object { $_ -match 'Git' }
   ```
   Fine: `StartName` equals the folder owner, or `safe.directory` lists `C:/apps/us-stock-reader` (or `*`), and a Git folder is on the
   MACHINE path. Otherwise tell me: the remedy is a machine setting (`git config --system --add safe.directory
   C:/apps/us-stock-reader` and/or adding Git to the machine PATH) and needs your approval.
2. **No machine-wide database variable** (the test step refuses to run if one points at production). Prints only set / not set:
   ```powershell
   foreach ($n in "DATABASE_URL","APP_ENV") { foreach ($sc in "Machine","User","Process") { $n + " (" + $sc + "): " + $(if ([Environment]::GetEnvironmentVariable($n,$sc)) { "SET" } else { "not set" }) } }
   ```
   Expect `not set` everywhere (the site reads them from `backend\.env`).
3. **Build tools:** `node -v; npm -v; C:\apps\us-stock-reader\backend\.venv\Scripts\python.exe --version; git --version`.
4. **PostgreSQL 17 tools** (already used for your restore test): `& "<folder>\pg_dump.exe" --version` must say 17.
5. **Disk:** at least 5 GB free on C: (the dump is ~65 MB, the frontend build and tests need a few hundred MB).
6. **The site's own folder is clean except the two known local items** (see step W1): `git -C C:\apps\us-stock-reader status --porcelain`.

## 2. The sequence (inside the window)
| # | Step | Needs | Success looks like |
|---|---|---|---|
| W0 | corrected preflight (read-only) | nothing | everything PASS except Working tree, Fresh backup (and Entry window before 20:00 ET) |
| W1 | **clean tree**: put the two local items aside | **APPROVAL** | `git status --porcelain` prints nothing |
| W2 | fresh dump `take_predeploy_dump.ps1` | **APPROVAL** | `DUMP VALIDATED`, SHA-256 printed |
| W3 | preflight again | nothing | `DECISION: GO` |
| W4 | point the branch at the release (git metadata only) | **APPROVAL** | `rev-parse @{u}` prints `27765413...` |
| W5 | `update.ps1` = the DEPLOYMENT | **APPROVAL** | "Running with new entries LOCKED" |
| W6 | verify while LOCKED | nothing | every item in section 4 true |
| W7 | unlock | **separate APPROVAL** | `unlocked: true` |
| W8 | first-session watch | nothing | section 6 |

### W0 - preflight (the corrected script, attached as `vps_preflight_FINAL.ps1`)
It now requires a **completely clean** working tree: the release tooling (`release_check.identify`, `/safety`) treats ANY
`git status --porcelain` output, tracked or untracked, as "uncommitted changes". With the modified `Data_Health_Check.xlsx`
and the four `backend\diag*.py` files present, the cut-over preflight would have refused and `/safety` would have blocked the
unlock. The earlier script wrongly tolerated those files; the corrected one does not.

### W1 - clean tree (APPROVAL; reversible, nothing is deleted)
The five items: `Data_Health_Check.xlsx` (modified, generated by a manual run of `data_health_check.py`; nothing in the running
site rewrites it) and `backend\diag.py`, `diag2.py`, `diag3.py`, `diag_claude.py` (untracked). `.env`, `.env.bak` and
`.env.seoul_*` are git-ignored and are NOT touched.
```powershell
$bk = "C:\audit-reports\server-local-files-" + (Get-Date -Format "yyyyMMdd-HHmm"); New-Item -ItemType Directory -Force -Path $bk | Out-Null
Copy-Item C:\apps\us-stock-reader\Data_Health_Check.xlsx $bk
Copy-Item C:\apps\us-stock-reader\backend\diag*.py $bk
git -C C:\apps\us-stock-reader stash push --include-untracked -m "local files put aside before the 2026-10-27 release"
git -C C:\apps\us-stock-reader status --porcelain          # must print nothing
```
Undo any time: `git -C C:\apps\us-stock-reader stash pop` (after the cut-over this restores them as local changes again, which
would block the next unlock, so only do it deliberately).

### W2 - fresh dump (APPROVAL)
`take_predeploy_dump_FINAL.ps1` (attached). Session pooler, port 5432, PostgreSQL 17 tools, `--schema=public`, custom format.
Validates: size, stability, `pg_restore --list`, the six key tables, SHA-256 (3 reads) + `.sha256` + manifest. Never overwrites,
never prints credentials. The restore test is not repeated (same procedure as the one that passed on 2026-10-26).
Run: `powershell -NoProfile -ExecutionPolicy Bypass -File C:\audit-reports\take_predeploy_dump.ps1`

### W3 - preflight again
The preflight accepts the dump for 60 minutes. Take W2 and W3 close together, and start W5 within the hour.

### W4 - point the branch at the release (APPROVAL; changes git metadata only, no file)
```powershell
git -C C:\apps\us-stock-reader fetch origin release/trdgo-us-safety-20261026
git -C C:\apps\us-stock-reader rev-parse FETCH_HEAD            # must print 277f30f849051471745f03b0b5401737fc5392d0
git -C C:\apps\us-stock-reader branch --set-upstream-to=origin/release/trdgo-us-safety-20261026 institutional-intelligence
git -C C:\apps\us-stock-reader rev-parse "@{u}"                # must print 277f30f849051471745f03b0b5401737fc5392d0
```

### W5 - the deployment (APPROVAL) - run as Administrator (it stops and starts the service)
**Do NOT run `C:\apps\us-stock-reader\deploy\windows\update.ps1` from the current checkout.** That file exists there but it is
the OLD version (no preflight, no entry lock): it would pull and restart the site WITHOUT the lock. Run the release's own
copy, extracted from the approved commit (it is the same script this runbook describes):
```powershell
git -C C:\apps\us-stock-reader show 277f30f849051471745f03b0b5401737fc5392d0:deploy/windows/update.ps1 | Out-File C:\audit-reports\update_release.ps1 -Encoding utf8
powershell -ExecutionPolicy Bypass -File C:\audit-reports\update_release.ps1 -Root C:\apps\us-stock-reader
```
What it does, in order, and where it can stop WITHOUT having touched the service: record the current commit; fetch; stage the
target in a temp worktree and run its preflight (window, paper positions zero, every open signal recoverable, release check,
clean tree) with the site's database settings for that one process; `git pull --ff-only`; `pip install -r requirements.txt`
(no dependency changed); `npm ci` + `npm run build` (the live site's `frontend\dist` is rebuilt in place: expect a brief
front-end glitch of about 30 seconds; take a copy of `frontend\dist` first if you want a fallback); the whole backend test
suite; then `release_tool.py cutover`: preflight again, stop service, install the lock tables and triggers (**LOCKED**), start
the service, wait up to 60 s for `/safety`. It never unlocks.

## 3. If something stops (every row keeps entries locked or the service untouched)
| Where it stops | State | What to do |
|---|---|---|
| preflight / release check | nothing changed | read the reason (named), fix, rerun |
| `git pull`, `pip`, `npm`, build, tests | service still runs the OLD code in memory; files may be new | fix and rerun `update.ps1` (idempotent). Do not reboot the server in between |
| cutover: stop fails | service state unknown, database unchanged | check `Get-Service USStockReader`; do not start anything by hand |
| cutover: lock install fails | service left STOPPED (never started unlocked) | fix the database error shown, run `release_tool.py cutover` again |
| cutover: start fails | locked, not running | read `service.err.log`; do not unlock |
| cutover: `/safety` missing/unsafe/wrong commit | running, locked | investigate with section 4; do not unlock |
| `/safety` answers but `commit` is empty | running, locked | the git / service-account issue of check 1 above |

### Rolling back to the old build
There is **no automatic rollback**: `36094e8`, `683fce1` and `main` all fail `release_check.py` (no mandatory 15-minute
confirmation). Preferred order: (1) fix forward behind the lock (`entry_lock.py lock "reason"`; exits keep working);
(2) return to the release commit that last passed; (3) break-glass, each time with YOUR approval: run the old build behind the
database lock, only while open paper positions are zero:
```powershell
cd C:\apps\us-stock-reader\backend
.\.venv\Scripts\python.exe entry_lock.py status            # locked true, both triggers present, open paper positions 0
Stop-Service USStockReader
git -C C:\apps\us-stock-reader checkout --detach 36094e89ff78c531bd6c8997208d31f02f1aafee
Start-Service USStockReader
```
The database rejects every new paper entry and live signal while the lock is on, so the old build cannot open an unconfirmed
entry (its boards may still SHOW BUY/SELL: treat them as not tradable). Go back to the release as soon as possible
(`git checkout institutional-intelligence`, restart) and unlock only through `release_tool.py unlock`.
`rollback-prepare` is stricter (it refuses while ANY paper position or live signal is open) and is for a rollback that must
leave an empty book. The database is never restored over live data without your approval; the dump is for a disaster and is
restored into a scratch database first.

## 4. Verification while LOCKED (W6) - all must hold before you consider the unlock
```powershell
(Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing).Content          # status ok, database connected
(Invoke-WebRequest http://127.0.0.1:8000/safety -UseBasicParsing).Content          # JSON, see below
cd C:\apps\us-stock-reader\backend
.\.venv\Scripts\python.exe entry_lock.py status                                    # installed true, locked true, two triggers, stale_unresolved = 16
.\.venv\Scripts\python.exe intraday_signal_v2.py stale                              # lists the stale rows (read-only)
git -C C:\apps\us-stock-reader status --porcelain                                  # prints nothing
git -C C:\apps\us-stock-reader rev-parse HEAD                                      # 277f30f849051471745f03b0b5401737fc5392d0
```
`/safety` must show: `confirm15_required` true, `can_be_disabled` false, `static_checks` true, `unsafe_build` null,
`running_matches_tree` true, `uncommitted_changes` false, `entry_locked` true, `commit` starting `27765413`,
`database_dialect` postgresql, `environment` production. An HTML page instead of JSON means the OLD build is still running.
Also, signed in as admin: `/api/uw/health` (status, 429 and concurrency counters), the boards (WAIT until a confirmed 15-minute
reading exists), and that the open live signals recovered by the new engine are managed (they are not closed by the date).

## 5. Unlock (W7, separate approval)
```powershell
cd C:\apps\us-stock-reader\backend
.\.venv\Scripts\python.exe release_tool.py unlock --url http://127.0.0.1:8000 --expect-commit 277f30f849051471745f03b0b5401737fc5392d0
```
It re-asks the RUNNING service over HTTP and refuses unless every item of section 4 holds. Unlocking only makes new entries
POSSIBLE: each still needs a confirmed closed 15-minute reading in the regular session; the paper tracker may open from 04:00
ET and the live engine from 10:00 ET. You may leave it locked until you have watched a first session.

## 6. First sessions after unlock
* Boards show WAIT where data is stale/missing/conflicting; BUY/SELL only with a confirmed 15-minute reading.
* `/api/uw/health`: OK, no `concurrent_limit_exceeded` bursts, queue waits small.
* The 4 recovered live signals (and any other carried row inside the window) close by their rules, once each; their P&L belongs
  to the session they were opened in (a past day's entry-date numbers may change; `realized_on_date` does not).
* The 16 stale rows stay open and reported; retiring them (`intraday_signal_v2.py expire-stale`, dry run first, then `--apply`) is
  a separate decision: it keeps the rows, writes no P&L, and needs your approval.
* Stop the scratch restore instance on port 5433 (`pg_ctl stop`) when you no longer need it.
* Old `.env.bak` / `.env.seoul_*` files: untouched; removal needs your separate approval.

## 7. Approvals you will be asked for (none is assumed)
A0 push 277f30f (fast-forward) | A1 stash the local files (W1) | A2 machine settings for git / PATH, only if check 1 says so | A3 run the backup (W2) |
A4 point the branch (W4) | A5 run update.ps1 = deploy (W5) | A6 unlock (W7) | A7 expire stale rows (optional) |
A8 break-glass rollback (only if needed) | A9 delete `.env` backups (optional, later).

## 8. What was rehearsed on the laptop (nothing on the VPS)
| Rehearsal | Result |
|---|---|
| Whole backend suite on `277f30f`: 1094 tests | 0 failed, 4 skipped (the opt-in PostgreSQL tests; they passed on `2776541`, whose application code is identical) |
| The real test step of `update.ps1` in a clean checkout with a production-style `.env` and no `.env.dev` | exit 0; the variables it sets are gone afterwards; the checkout stays clean. (Without the fix: 5 failures, so the deployment would have stopped there) |
| Frontend `npm run build` | built in 24 s |
| `pip install --dry-run -r requirements.txt` | resolves; the release changes no dependency file |
| The whole `update.ps1` against a local copy of the repository ("origin" + a "production" clone at the old commit, sqlite database, the same kinds of local files as the server) | with dirty tree and before 20:00 ET it REFUSED naming both reasons and changed nothing; after the clean-tree step and 20:00 ET it passed the preflight with 4 open late signals + 2 stale rows (recovered / reported), fast-forwarded `36094e8 -> 277f30f`, installed dependencies, built the frontend, passed the tests and reached the cut-over, which stopped at "could not stop the service" (a deliberately non-existent service name): database unchanged, nothing started |
| The new build started in a production-style environment | `/health` ok; `/safety` JSON as required (`confirm15_required` true, `can_be_disabled` false, `static_checks` true, `unsafe_build` null, `commit` `277f30f84905`, `uncommitted_changes` false, `running_matches_tree` true); the checkout stayed clean while it ran |
| Lock install + unlock | triggers on both tables, locked; `release_tool.py unlock` succeeded only on a clean build and **refused when the service had been STARTED with a dirty checkout** (`the running build has uncommitted changes`), staying locked |
| Preflight and backup scripts | 20 + 29 + 15 + 12 scenarios (all NO-GO / NOT VERIFIED except the all-valid ones) and 17 backup-script scenarios |
Not rehearsed (cannot be, off the VPS): `Stop-Service` / `Start-Service` of `USStockReader`; the service account's access to
`git`; the real Supabase pooler; the real Unusual Whales traffic after the start.

## 9. Things to know
* The cut-over's own failure text says "Entries stay locked" even when it stops at the **stop** stage; at that stage nothing
  has been locked yet and the service state is "unknown": check `Get-Service USStockReader` (the table in section 3 is accurate).
* `/safety` captures `uncommitted_changes` when the service STARTS. A clean tree at the start is therefore a hard prerequisite
  (W1). Local changes made after the start do not change what `/safety` says until the next restart, but a later restart with a
  dirty tree would block the next unlock.
* Local rehearsal leftovers on the laptop (safe to delete): `%TEMP%\trdgo-e2e-0352`, `%TEMP%\trdgo-fe-QIDk`,
  `%TEMP%\trdgo_rehearsal_v*.db`.
