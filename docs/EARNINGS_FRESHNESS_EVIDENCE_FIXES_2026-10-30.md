# Earnings freshness, timing and evidence fixes: isolated development result (2026-10-30)

Branch `fix/earnings-freshness-evidence`, based on `91b8fe92` (Phase 2A/2B, itself on the production base `38ca61d`). Nothing is
committed, pushed or deployed. All tests ran on mocks and in-memory SQLite. No production database, no provider request, no restart.

## What changed (files)

| File | Change |
|---|---|
| `backend/local_warmer.py` | Counts resumed/fresh names (status COMPLETED, not WARMING 0/N); a name is warm because its **cache age** is under the limit, not because a per-day flag says so; per-name refresh age by days-to-report (`max_age_for`); stops instead of spinning 200 empty passes. Gates, hourly caps, 429 cool-down and the 3-connection limit untouched. |
| `backend/freshness_policy.py` (new) | Per-kind freshness limits (estimates, history, price, market, options flow, dark pool, peers, ownership, news) by band (<=2 days, 3-10, >10); parameter -> kind -> provider map; `assess()` verdict; defaults overridable with `EARN_FRESHNESS_JSON`. Lives here, not in the pinned `tab_settings.py`. |
| `backend/earnings_clock.py` (new) | US-Eastern date, `days_to`, `refresh_countdown`, `event_status` (UPCOMING / POSSIBLY_REPORTED / REPORTED; REPORTED only on a verified timestamp or a passed date), `verified_release_utc` hook (returns None: no verified source exists). |
| `backend/model_prediction_service.py` | `capture()` judges each event (provisional release cut-off via `earnings_scoreboard.classify`, cache freshness via the policy). `EARN_CAPTURE_GUARD`: `off` / `shadow` (default: judge and log, write exactly as before) / `enforce` (skip stale, missing or post-cut-off rows; narrow the window to the warmer's). Eligibility uses the ET date; `snapshot_date` keeps its legacy server-date meaning. `last_capture_report` exposes counts. |
| `backend/prediction_evidence_service.py` (new) | Append-only `earnings_input_snapshots` + `prediction_evidence` (DB triggers reject UPDATE/DELETE); content-addressed input snapshots; revisions chained by `supersedes_id`. `EARN_EVIDENCE=on` to enable (default off: creates and writes nothing). |
| `backend/all_models_service.py` | Matrix `days_to` on the ET date; optional `inputs_updated_at` / `inputs_age_h` per row behind `EARN_ROW_FRESHNESS` (default off). |
| `backend/api_routes.py` | `/earnings/trade/{symbol}` recomputes the countdown at read time. |
| tests | `test_earnings_clock.py`, `test_freshness_policy.py`, `test_warmer_counting.py`, `test_capture_guard_evidence.py` (new). |

**Not touched:** every pinned scoring module (`test_scoring_unchanged` passes unmodified), all weights and thresholds, the eight models,
`tab_settings.py`, `earnings_trade_service.py`, historical records, `grade()` / outcomes (Phase 2A strict methodology reused as is).

## Test results

Full backend suite in the dev worktree: 932 passed, 12 skipped, 5 failed, 5 errors. The 5 + 5 are **identical to the base `91b8fe92`**
(environmental: `backend/tools` is git-ignored so `test_moves_replay` cannot import its tool; `test_dev_isolation` / `test_env_isolation`
need a developer `.env`). New tests: all pass.

## Per-fix summary

1. **Warmer counting** - root cause: resumed `done` names were skipped without being counted (`local_warmer._run_pass`). Reproduced
   (WARMING, complete=0, 201 passes), fixed, tested (first pass, resumed, partial resume, retry/partial, empty queue).
2. **Age enforcement** - the sticky day flag let a cache age past `MAX_AGE` all day (118 of 127 imminent events were 20-24 h old in
   production). Now age decides. Limitation: the existing live-hours request cap (400/h, 04:00-20:00 ET) still limits how fast names
   due in the morning can be re-warmed; the 12 h imminent age mainly changes behaviour after 20:00 ET. Request volume: see "Release preparation / R5"
   (the earlier one-line figure of ~21k/day was the ceiling from the single most expensive name, not the expectation).
3. **Coverage vs predictions** - enforce mode narrows the capture window to the warmer's window and skips uncovered / stale names with a
   recorded reason. Default `shadow` changes nothing yet.
4. **Silent stale signals** - capture verdicts + evidence; matrix rows can show the age of the data behind them (flag).
5. **Per-kind policy** - implemented for the warmer age and the capture gate. Limit: the cache has ONE build time per entry, so the age is
   an upper bound for every field; per-field source timestamps do not exist in stored payloads.
6. **Capture timing** - provisional BMO/AMC windows (from 2A) are the conservative cut-off; every evidence row says
   `PROVISIONAL_WINDOW_UNVERIFIED`. UNKNOWN timing on the report day is refused in enforce mode. No exact release time is claimed.
7. **Countdown** - ET date at read time for the trade endpoint and the All Models matrix. Limitation: each model's own tab still counts
   with the server clock (their files are pinned scoring modules); an owner-approved pin change would be needed.
8. **Outcome grading** - Phase 2A (`earnings_outcomes`, `earnings_scoreboard`) already grades BMO/AMC strictly, leaves unknown timing
   PENDING_TIMING and excludes midnight/date-only snapshots; reused, not changed. Extended-hours reactions need data that is not stored
   (UNVERIFIED / MISSING). Historical accuracy records were not rewritten.
9. **Evidence** - append-only, content-addressed, revision chain. `inputs_status = RELATED_EQUITY_INPUTS_ONLY`: only the shared Equity
   cache entry is snapshotted; each model's private intermediate values are NOT captured (a model's score is not reproducible from this
   alone). Nothing is back-filled.

## Deployment and rollback (needs approval; none performed)

1. Merge the branch behind defaults: warmer fix active; guard `shadow`; evidence `off`; row freshness `off`.
2. Deploy per the existing hash-checked process; no migration runs by default (evidence tables are created only when `EARN_EVIDENCE=on`).
3. Observe `last_capture_report` / the warning log in shadow for several capture runs.
4. Pre-requisite before enabling evidence: run the PostgreSQL DDL once on a throwaway PostgreSQL (UNTESTED here: only SQLite was available).
5. Then `EARN_EVIDENCE=on`, later `EARN_CAPTURE_GUARD=enforce` after reviewing what shadow would have blocked.
6. Rollback: set the three flags off / unset (no restart-dependent state) or redeploy the previous build; the warmer fix is self-contained.
   The evidence tables are additive and may be left in place or dropped.

## Remaining limitations (unchanged by this work)

- No verified earnings release timestamps; no extended-hours prices stored.
- Model-specific inputs are not captured; a model's score cannot be reproduced from stored data.
- Every pinned scoring file is untouched, so the individual model tabs keep the server-clock countdown.

## Pre-deployment verification (2026-10-30)

Environment: a throw-away PostgreSQL 17.11 (official EnterpriseDB binaries, same version as production) bound to 127.0.0.1:55432 only,
data directory in the session scratchpad; the application engine was forced to a local SQLite file; no production URL, `.env` or provider key
was ever loaded. The server is stopped and its data deleted after the run.

| Check | Result |
|---|---|
| Evidence DDL, triggers, inserts, revisions, history on PostgreSQL | PASS (34/34 checks in `pg_verify.py`, plus the 18 tests of `test_capture_guard_evidence.py` run on PostgreSQL) |
| UPDATE / DELETE / TRUNCATE rejected on both tables | PASS (TRUNCATE needed its own statement-level trigger: added after the first run showed row triggers do not cover it) |
| Existing schema and prediction history untouched | PASS: `model_predictions` columns, indexes, constraints identical before/after; 398 seeded history rows byte-identical; only the two new tables added |
| Shadow mode vs the baseline capture, same inputs | PASS: identical row count (224) and identical rows (622 total incl. history) |
| Evidence disabled by default | PASS: no tables created by a default-environment capture; only 1/true/on/yes enable it |
| Warmer respects the existing request caps | PASS: hourly cap, 429 cool-down, manual pause, capped-plan daily limit and "nothing extra when fresh" tests; every gate / budget function is source-identical to the base (ast comparison) |
| Evidence cost (local PostgreSQL, not the Frankfurt link) | 16 statements for a first 4,185-cell run, 3-4 for later runs (was one lookup + one insert per cell); about 3 MB per daily run (measured 6.4 MB for two full runs plus a revision run) |
| Backend suite | SQLite: 940 passed, 5 failed, 5 errors. PostgreSQL: 933 passed, 12 failed, 5 errors. Base: SQLite 867 / PostgreSQL 860 passed with the **identical** failure sets (environmental: git-ignored `backend/tools`, `SESSION_SECRET` dev guard, stale local SQLite twin table) |
| Diff review | no pinned scoring file, no deployment/config file, `main.py` and `tab_settings.py` untouched; no secret or production identifier in the diff; no ALTER/DROP/DELETE/UPDATE on existing tables |

---

# Release preparation (2026-10-30) - NOT DEPLOYED, awaiting explicit approval

## R1. What this release is

| Item | Value |
|---|---|
| Branch | `fix/earnings-freshness-evidence` (local only; **not pushed**) |
| Production now | `38ca61df` (per the 2026-10-28 deployment; Phase 2A preflight passed on `b4533203` but **2A is not deployed**) |
| Stack | `38ca61d` -> **Phase 2A** `b4533203` (outcome lifecycle, verified scoreboard, new table `earnings_event_outcomes`, frontend changes) -> **Phase 2B** `dc025951`, `91b8fe92` (flag `HISTORICAL_MOVES_METHOD`, default legacy) -> **this fix commit** |
| What deploying this branch means | It deploys **everything in the stack**. The 12 files of this fix are the only new part; Phase 2A/2B carry their own gates (fresh dump + scratch restore, push approval, CREATE privilege, 120-request first grading cycle, frontend build) from `docs/PHASE2A_DEPLOYMENT_PLAN_2026-10-29.md`. Either deploy 2A first as planned and this after, or deploy the stack in one controlled step after every 2A gate is green. |
| Defaults shipped | `EARN_CAPTURE_GUARD` unset = **shadow**, `EARN_EVIDENCE` unset = **off**, `EARN_ROW_FRESHNESS` unset = **off**. Set none of them. The warmer fix is active. |
| Migration at default settings | **none** (the two evidence tables are created only when `EARN_EVIDENCE` is switched on). Phase 2A's one `CREATE TABLE IF NOT EXISTS earnings_event_outcomes` still applies if 2A goes with it. |
| Frontend / dependencies / env files | not touched by this fix (the 2A part has frontend changes: it needs its own `npm run build`) |

Nothing in the 12 files changes a model, weight, threshold or any of the 30 SHA-pinned scoring files (`test_scoring_unchanged` passes unmodified).

## R2. Pre-deploy checklist (every item must be green; none has been run on the VPS)

| # | Gate | How |
|---|---|---|
| 1 | Explicit approval to push the branch (the VPS fetches from GitHub) | owner |
| 2 | Fresh logical backup: `pg_dump -Fc` via `docs/handover_2026-10-27/take_predeploy_dump_FINAL.ps1` (session pooler :5432, never overwrites, `pg_restore --list` check + SHA-256 sidecar) | record path, size, SHA-256 |
| 3 | Scratch restore of that dump into a throw-away database with equal row counts for `model_predictions`, `earnings_signal_snapshots`, `swr_cache` (the restore path has never been exercised) | `PASTE_scratch_restore_v4.ps1` |
| 4 | Record the current build: `git rev-parse HEAD` on the VPS (expect `38ca61df...`); keep the current `dist` ZIP if 2A's frontend is part of the same deploy | VPS |
| 5 | Baseline read-only measurements (so after-deploy numbers can be compared): `local_warm_state` row 2 (`calls_day`, symbols count), rows per `model` in `model_predictions` for the latest `snapshot_date`, `swr_cache` key counts for `earn:equity:` / `earn:trade:default:`, the 429 count of the last 24 h on the UW dashboard, `service.err.log` size | the existing read-only inspection scripts |
| 6 | Environment check: `APP_ENV=production`; `EARN_CAPTURE_GUARD`, `EARN_EVIDENCE`, `EARN_ROW_FRESHNESS`, `EARN_FRESHNESS_JSON`, `EARNINGS_LEGACY_SYNC`, `HISTORICAL_MOVES_METHOD` all **unset** | ENVCHECK script, names only |
| 7 | A SHA-checked deploy script for the new target SHA, written and reviewed (clone of `deploy_phase2a_b4533203.ps1` with Preflight / Deploy / PostCheck / Rollback modes). **Not written yet.** | produced after approval |
| 8 | Deploy window outside US market hours (see R5); the restart takes about 7 s | owner |

## R3. Deployment steps (after approval; one at a time, each SHA-checked)

1. `git push origin fix/earnings-freshness-evidence` (needs your explicit push approval).
2. Backup gates 2-4 above; record the results.
3. Script `-Mode Preflight` (read-only: clean tree, target object fetched, ENVCHECK, dump age, CREATE privilege).
4. Script `-Mode Deploy -Confirm 'DEPLOY <sha>'`: checkout detached, `npm run build` (only if the 2A frontend is included), restart `USStockReader`, health check.
5. Script `-Mode PostCheck`, then the smoke tests below.

Smoke tests (read-only): `/api/health` 200 and a protected route still 401; no new `Traceback` / `UndefinedTable` / `ECHECKOUTTIMEOUT` versus the baseline; `SIGNAL_AUDIT_STATS` keeps advancing; `local_warm_state` row 2 shows a pass that ends `COMPLETED` (not `WARMING` with complete 0); the next capture adds rows to `model_predictions` with the same per-model counts as the previous day (+-5 %); no table named `prediction_evidence` or `earnings_input_snapshots` exists; `/api/earnings/trade/<symbol>` returns `days_to_cached` next to a recomputed `days_to`.

## R4. Rollback

* Code rollback: on the VPS `git checkout --detach 38ca61df...` (or the SHA recorded in R2 item 4), restore the pre-deploy `dist` ZIP if the frontend was rebuilt, `Restart-Service USStockReader`, health check. About 7 s of downtime.
* Flags need no rollback at the shipped defaults. If a flag was ever turned on: unset it and restart. The evidence tables are additive and may stay (the application never reads them); dropping them is the owner's choice.
* Never restore the database dump for a code rollback. A dump restore is for data corruption only, into a scratch database first.
* Safe to leave in place after a rollback: rows the new code captured (they follow the old schema), and the Phase 2A table `earnings_event_outcomes`.
* After a rollback the old warmer returns (reports WARMING 0/N, sticky day flag): expected and known.

## R5. Production impact (estimates; no live provider call was made)

**Warmer refresh frequency.** Before: each of about 446 queued names was re-warmed once per provider day (20:00 ET reset), because the day flag blocked any earlier refresh. After: a name is re-warmed when its cache is older than 12 h (reporting within 2 days) or 20 h (everyone else), checked every 15 minutes. Practical effect: about 127 imminent names (0-2 days) may get a second refresh per day; the other ~320 stay at about one per day. Morning refreshes are metered by the unchanged live-hours cap (400 requests per hour, 04:00-20:00 ET), so most extra work lands after 20:00 ET, where the cap is 6,000 per hour and the nightly pass already runs.

**Provider request volume (Unusual Whales).**

* Observed: the 28 Oct warmer day used 10,448 requests for a 594-name queue (about 17.6 per name overall, including names skipped as fresh). Single fully refreshed names in the log used 41 to 163 requests.
* Extra per day = imminent names x requests per refresh. Lower bound about 127 x 17.6 = **2.2 k** (+21 % of the 28 Oct day); ceiling about 127 x 163 = **21 k** (the most expensive observed name, for every name). The true figure is unmeasured: read `calls_day` during the first full day (R6). The "~21 k" in an earlier line of this document was the ceiling, not the expectation.
* Limits unchanged and verified source-identical: unlimited daily plan (verified 2026-10-23), 3 simultaneous requests per token shared with the live trading engine and the other local app, warmer at priority 4 in one background slot that yields, hourly caps 400 / 6,000, 429 cool-down. The binding constraint is concurrency, not the daily total.
* Finnhub: each refresh also does one `hit_rate_bias` fetch (6 h cache), paced at 55 per minute; the extra count scales with the extra refreshes (about 127 per day).
* The capture guard (shadow) and evidence make **no provider call**. In shadow mode the guard reads each cached analysis (up to about 930 single-row database reads of about 5 KB per capture run, twice a day, one connection for tens of seconds at Frankfurt latency; unmeasured). Evidence is off at default; if enabled later: about 3 MB per daily run (measured on local PostgreSQL) and 3 to 16 statements per run.
* Day-one catch-up: at deploy time any cache older than its new age becomes due; in live hours the 400 per hour cap meters it, after 20:00 ET it drains in the normal nightly pass. Deploying after the close avoids contention with the live engine.

## R6. Monitoring plan

| What | Where | Normal | Act if |
|---|---|---|---|
| Warmer pass result | `local_warm_state` row 2 (`status`, `complete`, `total`, `failed`, `calls_day`) | `COMPLETED`, `complete` = `total` | `WARMING` with `complete` 0 after a full pass (old bug back); `failed` above 5 % of total |
| Daily request count | `calls_day` in the same row; UW dashboard "Request health" | within +45 % of the pre-deploy day | above 2 x baseline, or more 429s than the pre-deploy 24 h |
| Guard verdicts | `service.err.log` line `earnings capture guard (shadow): {...}` (logged only when something would have been blocked) | appears at times; STALE / NO_CACHE small relative to rows | NO_CACHE or STALE for most rows at the 04:26 UTC batch |
| Capture output unchanged | rows per model in `model_predictions` per `snapshot_date` | within +-5 % of the previous day | a drop (shadow would not be neutral: roll back) |
| Cache ages | `swr_cache` age of `earn:equity:*` for events within 2 days | under 12-14 h after the nightly pass | still 20-24 h two days after deploy |
| Database connections | Supabase dashboard | well below the 15-connection limit | above 12 sustained |
| Errors | `service.err.log` | no new Traceback family | any new family; `ECHECKOUTTIMEOUT` rising |
| Live engine health | `SIGNAL_AUDIT_STATS`, trades count / max id | advancing | stalls |

Review after the first night, the first full day and one week; record the numbers next to the R2 item 5 baseline.

## R7. Limits that must stay visible before anything beyond the defaults is enabled

1. **Model-specific inputs are not captured.** The evidence design snapshots only the shared Equity cache entry (`inputs_status = RELATED_EQUITY_INPUTS_ONLY` in every row). Each model's own intermediate values, thresholds and gates (for example the `claude_new` Implied-Move gate, its 5-step quantisation and its price-move-based reaction input) are not stored, so no model's BUY/SELL/NEUTRAL is reproducible from the evidence alone. Only the Equity engine's own score is.
2. **`EARN_CAPTURE_GUARD=enforce` must not be enabled** without a separate live shadow-data review. That review needs per-cell verdicts, which are stored only when `EARN_EVIDENCE=on`. The sequence is: deploy at defaults -> watch the counts -> (separate approval) turn evidence on -> review what shadow would have blocked over several capture runs, including the 04:26 UTC and 16:26 UTC batches -> (separate approval) decide on enforce. Enforce changes what is written to `model_predictions` (it skips rows and narrows the window); in the synthetic PostgreSQL run it wrote 101 rows against 224 for shadow.
3. Ages are upper bounds (one build time per cache entry); release times and extended-hours prices are not stored; every cut-off is provisional.
4. The individual model tabs keep the server-clock countdown (pinned files).
5. The PostgreSQL verification used a local 17.11 server and synthetic caches, not production data volume or latency.
