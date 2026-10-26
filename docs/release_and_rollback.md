# Release and rollback: no state may open an unconfirmed entry

Owner's rules: the 15-minute candle confirmation is mandatory for every NEW regular-session entry and has NO switch
(WAIT outside the regular session is approved); a rollback must never silently re-enable entries without it; exits,
stops, targets and data collection are never blocked.

## Facts this procedure is built on (verified in the code, 2026-10-24)

* **Paper only.** TRDGO-US has no broker or order code: `demo_trades` (paper blotter) and `intraday_sig_v2` (a signal
  log with simulated P/L) are the only "positions". `ibkr_vix.py` reads one market-data series; nothing places orders.
  Stopping the service cannot leave a real-money position unmanaged.
* **If the service stops while a paper position is open** it simply stops being updated (price, stop, target, score
  exit, timed exit) and **resumes on restart, whatever date it was opened** (see docs/position_lifecycle.md: carried
  positions are monitored and closed by their rules, never because the date changed). A position can therefore
  outlive a swap; the procedure still swaps only with ZERO open positions so that nothing is unmonitored while the
  service is down and so that a rollback to an older build (which cannot carry positions) is never started with one.
* **Entries are possible only** on weekdays 04:00-20:00 ET (tracker; the live engine only 10:00-15:30 ET). Outside
  that, no build can open an entry because of the TIME - the deployment window.
* **Database engine:** PostgreSQL (Supabase) behind the transaction pooler (`:6543`) per the production URL's shape; the
  development project is PostgreSQL 17.11. Confirm the server's own on `/safety` -> `database_dialect` or
  `python backend\entry_lock.py status` -> `dialect` (read-only).

## What enforces the confirmation

| Layer | What it does |
|---|---|
| `intraday_15m.entry_check` | The one gate; FAIL CLOSED (any error = no entry, never an exception). Allows only a fresh, valid, agreeing reading in the regular session and, at the moment of opening, an open entry lock. |
| Boards / `tradable()` | Unconfirmed BUY / SELL -> WAIT; `tradable` re-asks the gate on every call. |
| Paper-trade tracker, live engine | Ask again at the moment of opening (the engine also right before its INSERT). Exits never call the gate. |
| `entry_lock` | App check + database triggers rejecting INSERTs on `demo_trades` / `intraday_sig_v2` for ANY build. UPDATEs (exits) untouched. Tested on PostgreSQL 17 (opt-in test). |
| Boot self-check | The build runs `release_check` over its own files at start; a partial deploy refuses ALL entries (`BUILD_FAILED_SAFETY_CHECK`). |
| `/safety` (public, minimal) | What the RUNNING process reports: flags, short commit, whether it is the commit on disk. |
| `release_tool.py` | preflight (window, open positions, release check, identify), cutover (stop -> lock -> start -> verify), unlock (only after verifying the running service), rollback-prepare. No skip flags exist. |
| `update.ps1` | Judges the target BEFORE pulling; pulls, builds, tests; cuts over with entries LOCKED; never unlocks. |

## 0. Identify what production runs (read-only; never assume `main`)

    cd C:\apps\us-stock-reader
    git rev-parse HEAD ; git status --porcelain
    backend\.venv\Scripts\python.exe backend\release_check.py --identify      (after the first release; before it use a stage worktree)
    curl http://127.0.0.1:8000/safety                                          (404 = a build that predates the safety work)
    backend\.venv\Scripts\python.exe -c "import database; print(database.engine.dialect.name)"   (run with the server's .env)

Local `main` (5a0160d) and `origin/main` (f8045af) differ; neither is evidence of what the server runs. Record the
server's commit as the **rollback reference**.

Read-only SQL for the positions (psql / any client, production credentials stay on the server):

    SELECT count(*) FROM demo_trades WHERE status = 'OPEN';
    SELECT count(*) FROM intraday_sig_v2 WHERE is_open = TRUE;

## 1. Zero-entry-risk release (the service is not stopped until the impact is known)

Operational impact of the swap: the website and all data collection pause for the stop/start (about a minute); the
warmer, the feed and the 15-minute collection resume at boot (boot_guard restores their schedule); paper positions: none
may be open. Run ONLY inside the window; the tools refuse otherwise.

1. **Window.** `python backend\release_tool.py window` -> `entries_impossible_by_time: true` (Friday 20:00 ET to
   Monday 04:00 ET, or any night 20:00-04:00 ET; Dubai = ET + 8 h until 1 Nov, + 9 h after). Holidays are NOT assumed closed.
2. **Positions.** Both SQL counts above are 0. If not: do not proceed; let the running build close them (timed exit 16:00 ET).
3. **First release only** (the server has no checker yet): stage the target and run its preflight from there:
   `git worktree add C:\apps\stage <target>` then
   `C:\apps\us-stock-reader\backend\.venv\Scripts\python.exe C:\apps\stage\backend\release_tool.py preflight --repo C:\apps\us-stock-reader --target <target>`.
   (Later releases: `update.ps1` does this itself.)
4. **Run `deploy\windows\update.ps1`.** It records the rollback reference, preflights the staged target, pulls,
   builds, tests, then `release_tool.py cutover`: stop -> install the lock (locked) -> start -> wait for `/safety`.
   Any failure leaves the service STOPPED or entries LOCKED, never open.
5. **Verify the running build** (entries are still locked): `/safety` shows `confirm15_required true`,
   `can_be_disabled false`, `static_checks true`, `unsafe_build null`, `running_matches_tree true`,
   `uncommitted_changes false`, `entry_locked true`, `commit` = the target.
6. **Unlock** - only through `python backend\release_tool.py unlock --url http://127.0.0.1:8000 --expect-commit <target>`
   (also `python backend\entry_lock.py unlock`). It re-checks the RUNNING service and refuses otherwise.
7. The first regular session: boards show WAIT until a CONFIRMED 15-minute reading exists for a name; entries open
   only then. Watch `/api/uw/health` and the boards.

Nothing between step 1 and step 7 can open an unconfirmed entry: the window excludes entries by time; the old build is
stopped; the new one starts locked; unlocked it still needs a CONFIRMED reading; a partly deployed build refuses all.

## 2. Failure handling (each tested in `tests/test_release_workflow.py`)

| Failure | What happens |
|---|---|
| Not in the window / a position is open / DB unreadable / target fails the check / checkout dirty | preflight refuses; the service is untouched. |
| Cannot stop the service | release stops; the database is unchanged. |
| Lock or triggers cannot be installed (permission, missing table) | service left STOPPED, never started unlocked. |
| Service does not start | entries stay locked; follow the rollback procedure; do not unlock. |
| Service starts but `/safety` is missing, unsafe or not this commit | entries stay locked. |
| Confirmation data missing or stale after unlock | no entry (`MISSING` / `STALE`); the board shows WAIT. |
| The gate itself errors | no entry (`GATE_ERROR`); exits still commit. |

## 3. Rollback

Preferred: stay inside a passing build and switch features (`UW_MAX_INFLIGHT`, `INTRADAY_FEED["split"]`,
`INTRADAY_TIERS["enabled"]`, warmer caps). To stop all new entries at once: `python backend\entry_lock.py lock "reason"`
(exits keep working). Never remove the 15-minute confirmation - there is no switch.

Rolling back to a build that fails `release_check.py` (`683fce1`, `5a0160d`, `f8045af` all fail):

1. `release_check.py <target>` -> FAIL: it has no mandatory confirmation. The normal release path refuses it.
2. In the window with the new build still running: `python backend\release_tool.py rollback-prepare` - REFUSES (exit 2)
   while any position is open (an older build cannot manage exits through the trigger: its blocked INSERT also fails
   the transaction that carries its exits). Let the new build close them first.
3. When it succeeds the book is flat and the lock is on. Stop the service, check out the target, start it. The database
   rejects every INSERT, so even a build that knows nothing about the lock cannot open an entry; its boards may still
   SHOW BUY / SELL (unconfirmed) - treat them as not tradable.
4. If a pre-confirmation build must serve before step 2 can complete, keep the service STOPPED.
5. Return to a passing build as soon as possible; unlock only on a passing build via `release_tool.py unlock`.

## 4. Never

* Never deploy or roll back to a revision that fails `release_check.py` outside this procedure.
* Never assume `main` / `origin/main` is production.
* Never edit the lock tables by hand while a build older than the safe one runs with open positions.
