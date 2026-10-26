# Production cut-over plan: `36094e8` -> `release/trdgo-us-safety-20261026`

Target: `C:\apps\us-stock-reader` on the production VPS (service `USStockReader`, port 8000). Nothing in this document has
been executed. **The cut-over runs only after the owner's explicit approval.** Read `docs/release_and_rollback.md` for
how the tools work; this page is the exact sequence for THIS server.

## Facts (VPS audit, 2026-10-26, read-only)

| Item | Value |
|---|---|
| Server build | `36094e8` on branch `institutional-intelligence` (= `origin/institutional-intelligence`), an ANCESTOR of the release (3 commits behind, fast-forward) |
| Safety on the server | none: no `/safety`, no entry lock, no `release_check.py`, no 15-minute confirmation |
| Local changes on the server | `Data_Health_Check.xlsx` (modified) and `backend/diag*.py` (untracked). No application code. Left untouched |
| Database | PostgreSQL 17.11, Supabase transaction pooler `:6543`, `APP_ENV=production`, 632 MB |
| Open positions at audit (Mon 13:07 ET) | 84 paper positions (0 carried), 16 live signals |
| Token | one Unusual Whales token; no other consumer on the VPS; `.env.bak` / `.env.seoul_*` left in place (removal needs separate approval) |
| Dependencies | `requirements.txt` and the frontend lockfile are unchanged by the release |
| Schema | no table or column changes. New: `trade_entry_lock` table and two triggers (installed by the cut-over). `NO_PRICE` is a new status string in an existing column; `exit_price`, `profit` and `profit_pct` are already nullable |

## What happens to the existing open positions

* **Nothing closes them except their own rules.** No tool in this release closes, deletes, resets or rewrites a position.
* The server's CURRENT build closes each paper position at its timed exit (16:00 ET) or earlier by stop / target / score.
  The preflight REFUSES to proceed while any `demo_trades` (status OPEN) or `intraday_sig_v2` (is_open) row exists,
  so the cut-over simply waits until the book is flat. If positions are still open at 20:00 ET (an old-build bug, or a
  position opened after hours), **do not force it**: report it and decide below.
* After the cut-over the NEW build tracks every open position of any date (carry-over), applies the same stop / target /
  score / timed rules, and books gap fills at the first reliable price. History, P&L and records are untouched.
* **Owner decision, only if the book cannot reach zero:** a forward cut-over with positions open is technically safe
  (the new build resumes monitoring them) but the preflight forbids it by design (it protects the ROLLBACK path, which
  cannot manage positions). Allowing it would be a code change plus your approval; it is NOT assumed here.

## Before the window (read-only; any time)

1. **Backup (BLOCKER until done).** The tools never touch backups, and the VPS audit could not see Supabase's own.
   a. Supabase dashboard -> Database -> Backups: note the plan, the latest backup time, and whether point-in-time
      recovery is on. Write the result down.
   b. A logical dump taken from the VPS just before the window (session pooler `:5432` URL; pg_dump cannot use the
      transaction pooler). Check first: `Get-Command pg_dump`. Use the server's own connection string, never paste it:

          pg_dump "<session-pooler URL>" -Fc -f C:\audit-reports\backups\trdgo-<yyyymmdd-hhmm>.dump
          Get-Item C:\audit-reports\backups\*.dump | Select Name, Length, LastWriteTime

   c. Recovery check (never against production): restore the dump into a scratch / development database with
      `pg_restore --no-owner -d <scratch URL> <dump>` and compare `SELECT count(*) FROM demo_trades`.
2. **Pooler compatibility.** Verified on the DEVELOPMENT project through its `:6543` transaction pooler (psycopg with
   `prepare_threshold=None`, per-transaction `SET LOCAL search_path`): the lock triggers block INSERTs, allow exits and
   updates, and the install is idempotent (`TRDGO_PG_TRIGGER_TEST=1 TRDGO_PG_TEST_POOLER=1`). Production's own pooler was
   not touched; the cut-over's own verification step (below) re-checks the lock on the server.
3. **Commit identity.** On the server: `git -C C:\apps\us-stock-reader rev-parse HEAD` must print the `36094e8...` SHA
   recorded above (the rollback reference). Anything else: stop and report.
4. **Fetch only** (no code change): `git -C C:\apps\us-stock-reader fetch origin` and
   `git -C C:\apps\us-stock-reader rev-parse origin/release/trdgo-us-safety-20261026` = the SHA reported after the push.

## The window

Friday 20:00 ET (Saturday 04:00 Dubai until 1 Nov, 05:00 after) until Monday 04:00 ET, or any night 20:00-04:00 ET.
Entries are impossible by time then. Holidays are not assumed closed.

1. `python backend\release_tool.py window` -> `entries_impossible_by_time: true`.
2. Both counts are 0:

       SELECT count(*) FROM demo_trades WHERE status = 'OPEN';
       SELECT count(*) FROM intraday_sig_v2 WHERE is_open = TRUE;

3. Backup (1b) taken within the last hour; its size is sensible.
4. Point the local branch at the release WITHOUT changing any file yet (a config change only):

       cd C:\apps\us-stock-reader
       git branch --set-upstream-to=origin/release/trdgo-us-safety-20261026 institutional-intelligence

5. Run `deploy\windows\update.ps1`. It stages the target, runs `release_check` + the window + open-position checks on it,
   then pulls (`--ff-only`), installs dependencies, builds the frontend, runs the backend tests, and cuts over: stop ->
   install the entry lock (starts LOCKED) -> start -> wait for `/safety`. It never unlocks.
   `Data_Health_Check.xlsx` and `backend\diag*.py` are not in the release and stay as they are.
6. Verify with entries still LOCKED:
   * `http://127.0.0.1:8000/health` -> `status ok`, `database connected`
   * `http://127.0.0.1:8000/safety` is JSON with `confirm15_required true`, `can_be_disabled false`, `static_checks true`,
     `unsafe_build null`, `running_matches_tree true`, `uncommitted_changes false`, `entry_locked true`, `commit` = target,
     `database_dialect postgresql` (an HTML page here means the OLD build is still running)
   * `backend\.venv\Scripts\python.exe backend\entry_lock.py status` -> `installed true`, `locked true`, both triggers
   * signed in as admin: `/api/uw/health` status and 429 counters; the boards show WAIT until a confirmed 15-minute
     reading exists
   * re-run `vps_readonly_audit.ps1` (fixed copy) and compare with the 2026-10-26 report
7. **Unlock only on your explicit approval**, through the tool that re-checks the running service:

       python backend\release_tool.py unlock --url http://127.0.0.1:8000 --expect-commit <target SHA>

## If the deployment fails

| Where it fails | State | What to do |
|---|---|---|
| Preflight / tests / frontend build | nothing was stopped | fix and retry; the server is unchanged except possibly `git pull` having moved the files (re-run `git status`) |
| Stop, lock install or start | service STOPPED or LOCKED, never open | read the message; do not unlock; keep the service stopped if it cannot start |
| `/safety` missing or unsafe after start | LOCKED | investigate; do not unlock |
| Any doubt about the new build | LOCKED | roll back as below |

**Rollback never uses `36094e8`, `683fce1`, `main` or `origin/main` to re-open entries.** They all fail
`release_check.py` (no mandatory 15-minute confirmation). The recovery targets, in order:

1. Stay on the release and fix forward behind the lock (`entry_lock.py lock "reason"`; exits keep working).
2. Return to the release commit that last passed (keep its SHA from the update log).
3. Only if the release cannot run at all: `release_tool.py rollback-prepare` (refuses while a position is open), then
   stop the service, check out the older build and keep it LOCKED (the database rejects every INSERT regardless of the
   code) - the old build cannot place an entry. Return to a passing build as soon as possible.

The database is never restored over live data without the owner's approval. A restore uses the dump from step 1b into a
scratch database first.

## What is NOT verified until the window

The production `/api/uw/health` figures (latency, queue waits, 429s) and live 38-stock timing exist only after the new
build runs against the real provider; the entry-lock behaviour on the production pooler is verified by the cut-over
itself; Supabase backup coverage is the owner's dashboard read.
