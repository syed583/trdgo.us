# TRDGO.US — Audit-only deployment: GO / NO-GO report

**Decision: NO-GO for production today — code is ready (PASS), four production-side gates are NOT VERIFIED** (database version, fresh backup + restore proof, schema/permission readiness run, branch availability on the server). Nothing was changed in production: no migration, push, deploy, restart, environment edit or activation. `INTRADAY_WORKFLOW` stays `legacy`; V2, shadow, the 5/15-minute gates, opening window, liquidity/spread limits, position caps and loss limits are untouched and off.

Hotfix: branch `hotfix/audit-only-36094e8`, commit **`ea1cc0443d601f80148edcd768b75d748eb6ea3d`** (supersedes `a95e7f4`), on production baseline `36094e89ff78c531bd6c8997208d31f02f1aafee`. Local only, not pushed.

## 1. Exact hotfix diff against production 36094e8 — PASS
11 files, +1,044 / −3:
| File | Nature |
|---|---|
| `backend/signal_verification.py` (new, 394 lines) | the independent verifier, attribution check, report |
| `backend/signal_verification_cli.py` (new) | read-only `report` / `history` / `stats` |
| `backend/demo_trade_service.py` | `_row_map` carries `params` and `data` (read-only extras); after the tick's `s.commit()` + `s.close()` a guarded `_audit_signals(...)`; `_ensure_audit_table()`. Only deleted lines: the old `pass` in `ensure_schema`'s except (now `return`, identical effect) and the old 3-key `_row_map` tail. The entry loop gains only `have, have0 = set(), set()` / `have0 = set(have)` (read-only copies). |
| `backend/models_demo_trades.py` | one new model `IntradaySignalAudit`; `DemoTrade` untouched |
| `backend/tab_settings.py` | `INTRADAY_VERIFICATION` (mode from env `INTRADAY_VERIFICATION_MODE`, default `off`; only `off` / `audit` exist) |
| `backend/database.py` | table name added to the development-isolation list (no effect in production) |
| `backend/migrations/2026_10_27_A_audit_only.sql` / `_ROLLBACK.sql` | the only schema change: one table + one index |
| `backend/migrations/2026_10_28_A_audit_role_TEMPLATE.sql` | optional least-privilege role (template, not executed) |
| 2 test files | tests |
Verified: **no** reference to `INTRADAY_WORKFLOW`, `entry_session`, shadow tables, `intraday_15m`, entry lock or opening rule anywhere in the diff; the hotfix needs no V2 evidence columns on `demo_trades`.

## 2. The six scoring models are unchanged — PASS
`git diff 36094e8 hotfix` is **empty** for `chatgpt_intraday_service`, `claude_pro_intraday_service`, `intraday_grok_service`, `kimi_intraday_service`, `meta_intraday_service`, `gemini_intraday_service`, `claude_last_service`, `intraday_model`, `intraday_indicators`. The strategies stay independent (no shared scoring model). The verifier agrees with each model's *real* scorer on 400 random inputs per model (2,400 rows, ≈1,700 actionable BUY/SELL): 0 integrity failures; a Grok row verified as Kimi fails the weight check (model identity).

## 3. Test results — PASS (with 3 explained baseline failures)
* Hotfix build, full suite: **654 passed, 12 skipped, 3 failed**. New tests: 14 (audit-only) + 10 (continuity) all pass.
* The 3 failures — `test_dev_isolation::test_adds_ssl_when_missing`, `test_dev_isolation::test_never_falls_back_to_production_env`, `test_env_isolation::test_a_local_write_lands_in_the_twin_never_in_the_real_table[demo_trades]` — fail **identically on the untouched 36094e8** in the same clean checkout (verified by stashing the change). Cause: a fresh checkout lacks the git-ignored `backend\.env.dev` (`DEV GUARD: SESSION_SECRET is required`), and the third trips on the resulting `_local` twin fixture. They exercise the development-database safeguards, not scoring or trading, and they pass in the working checkout that has `.env.dev` (feature branch: 1254 passed, 4 skipped; `release_check.py` PASS).
* Mathematical tests are not profitability claims.

## 4. Production database version and migration readiness — NOT VERIFIED (script ready)
* The version line of the earlier historical report was cut off in the paste, so **the actual PostgreSQL version is not confirmed** (project records say Supabase PostgreSQL 17).
* Known from that report: 829 `demo_trades` rows, 0 OPEN, no V2 columns, no audit objects, role can CREATE and has DML on `demo_trades`, **not superuser but holds CREATEDB, CREATEROLE and BYPASSRLS — FAIL against least privilege**.
* `phaseC/prod_readiness_checks.py` (read-only, no DDL even rolled back) verifies: version 17, primary, read-only session, CREATE right, role over-privilege, connection headroom, no object named like the audit table/index/sequence (collisions incl. `_local` twins), `demo_trades` columns/types exactly as the production build declares, no heavy locks or long transactions on `demo_trades`. Tested on a throw-away PostgreSQL: 11 pass; the 2 expected fails there (it is PG 18 and a superuser).
* Name-collision analysis: `intraday_signal_audit` and `ix_intraday_signal_audit_day` do not exist (production has only `intraday_sig_v2*`); in development the isolation rewrite maps them to `intraday_signal_audit_local` / `ix_intraday_signal_audit_local_day` — distinct names (the index name contains the table name by design).
* Least privilege (recommended, optional): the audit can run as its own role through `AUDIT_DATABASE_URL` — a role limited to SELECT on `demo_trades` and SELECT/INSERT on the audit table (template provided, tested that the audit uses it). Not applied; creating a role and a secret is a production change that needs your approval. Without it the audit uses the application's existing (broad) role.

## 5. Backup and rollback — backup NOT VERIFIED; rollback PASS (rehearsed)
* **Backup: not taken** (no authority/access). Use the validated `take_predeploy_dump_FINAL.ps1` immediately before the migration; the dump must be < 30 min old. **Restore proof is NOT VERIFIED**: the script validates the dump by reading it back (pg_restore listing), but an actual restore into a scratch database has not been done; recommended before the change (a scratch Supabase project or local PostgreSQL 17).
* Rollback of the migration: `..._A_audit_only_ROLLBACK.sql` rehearsed on a real PostgreSQL engine (PGlite = PG 18): 26/26 checks — only the audit table is dropped, `demo_trades` columns, indexes and every row (md5 over all rows) are byte-identical before/after apply and after rollback, re-apply works, apply is idempotent. Caveat: not PG 17, and lock contention with live trading could not be exercised (single-connection server); mitigated by `lock_timeout 3s` + `statement_timeout 30s` in the file (fails fast, rolls back, changes nothing).
* Because no existing table is modified, a restore of the dump is only a last resort.

## 6. Trading continuity and failure isolation — PASS (each tested on the production build and on PostgreSQL)
| Requirement | Evidence |
|---|---|
| Legacy commits independently of the audit | Tick commits and closes its session first; the audit uses a separate session |
| The audit runs after the commit | test: inside the verifier a **second database connection already sees all 10 trades** |
| Audit failure does not stop the next cycle | table missing (every write fails) → trades identical, next tick normal; on PostgreSQL a trade opened right after the failed audit |
| Audit DB write failure does not affect `demo_trades` | md5 of all rows identical before/after; no open transaction/lock left |
| A crashing / failing verifier | counted (`verify_errors`) and skipped per signal; other signals still audited; trades identical |
| No duplicate trades; existing trades unmodified | audit on vs off: trade list identical to the cent; the audit writes only its own table; attribution check flags >1 trade per strategy/symbol/day |
| Time budget | `max_seconds` 5 s; 0-second budget → nothing audited, nothing raised, trading untouched; PostgreSQL session `lock_timeout 2s`, `statement_timeout 5s`; 240 signals audited in 0.11 s |
| Failing signal still trades | a tampered-weight signal is recorded `FAIL` and traded exactly as before |
| Off by default; no enforcing mode | `enforce`/`block`/empty are inert; verified in tests |
| Restart | see §7; positions are persisted; a restart mid-tick aborts only that uncommitted tick, redone on the next |
No check failed, so no NO-GO from this section.

## 7. Deployment commands and restart impact — NOT VERIFIED on the VPS (prepared, not rehearsed there)
Pre-conditions (each needs your explicit approval): push `hotfix/audit-only-36094e8` to the remote (the server fetches from it); readiness script PASS; fresh validated dump; restart window between 20:00 and 04:00 ET.
```
# 1 read-only readiness (can run now)       -> paste block provided with this report
# 2 backup
powershell -File C:\audit-reports\take_predeploy_dump_FINAL.ps1
# 3 migration A (one transaction, fails fast); dry run first
python <phaseC>\apply_sql.py --root C:\apps\us-stock-reader --file 2026_10_27_A_audit_only.sql
python <phaseC>\apply_sql.py --root C:\apps\us-stock-reader --file 2026_10_27_A_audit_only.sql --apply
# 4 code (mode still off: no env line yet)
cd C:\apps\us-stock-reader
git status --porcelain                         # must print nothing
git rev-parse HEAD                             # must be 36094e89ff78c531bd6c8997208d31f02f1aafee
git fetch origin hotfix/audit-only-36094e8
git rev-parse FETCH_HEAD                       # must be ea1cc0443d601f80148edcd768b75d748eb6ea3d
git diff --name-only 36094e89ff78c531bd6c8997208d31f02f1aafee FETCH_HEAD     # must be exactly the 11 files of section 1
git merge --ff-only FETCH_HEAD
backend\.venv\Scripts\python.exe -m py_compile backend\signal_verification.py backend\demo_trade_service.py backend\models_demo_trades.py backend\tab_settings.py
Restart-Service USStockReader                  # restart #1 (audit still off)
# 5 activate (separate go): append ONE line to backend\.env, then restart #2
Add-Content backend\.env "INTRADAY_VERIFICATION_MODE=audit"
Restart-Service USStockReader
```
Restart impact: the API is unavailable for roughly 10–30 s per restart; the paper-trade tracker misses at most one 15–60 s cycle; nothing is lost because positions are persisted and the tick is transactional. Doing step 4 and 5 as one restart is possible but I recommend two so a problem can be attributed. `backend\.env` has duplicate `INTRADAY_MODEL` lines (unresolved, unrelated); the new line is appended at the end and affects nothing else. The server's working tree holds a stash of five local files from earlier phases; the tree must be clean before step 4.

## 8. Post-deployment validation checklist
Immediately after each restart: service Running for 2 minutes; no Traceback in the service log; all six boards load; Strategy Trades page loads and shows the existing trades unchanged (count, entry prices, statuses); `demo_trades` row count ≥ before and no row changed except by normal tracking. After activation, through the next entry window: new BUY/SELL trades still appear under the correct strategy; `SIGNAL_AUDIT_STATS` lines appear (cycles increasing, `write_errors` 0, `verify_errors` 0); `intraday_signal_audit` fills; the first six-model report (§10 of the plan).

## 9. Rollback triggers and procedure
Triggers (any one): service not Running 2 minutes after restart; Traceback mentioning `signal_verification` / `_audit_signals` / `demo_trade_service` loops; Strategy Trades stops adding trades while the boards show qualifying BUY/SELL; a trade changed/duplicated that the old logic would not; `write_errors` or `verify_errors` growing every cycle *with* any trading symptom (audit-only errors alone are a reason to switch the audit off, not to roll back code).
Procedure: (1) remove the env line (or `off`) and restart — trading never depended on the audit; (2) if needed `git merge`-reset the server back to `36094e89…` (`git reset --hard 36094e89ff78c531bd6c8997208d31f02f1aafee`) and restart; (3) optional: `apply_sql.py --file 2026_10_27_A_audit_only_ROLLBACK.sql --apply` (drops only the audit table; skip to keep the evidence); (4) last resort: restore the dump (not expected: no existing table is modified). Never roll back to `683fce1` / `main`.

## 10. Outstanding risks and limitations
* **Database version, backup, restore proof, schema/permission readiness: NOT VERIFIED** — they gate the GO.
* The application role is over-privileged (CREATEDB, CREATEROLE, BYPASSRLS): FAIL against least privilege; an optional dedicated audit role is provided but not applied.
* On this build there are no source timestamps: **staleness is reported as NOT ASSESSED**, never PASS; only *missing* evidence (eligible coverage) is measurable. Missing readings scored neutral are not counted as verified evidence.
* Historical parameter-weight verification is impossible for the 829 existing trades (contributions never stored): they are reported only for stored score / side / price checks (821 PASS, 0 FAIL, 8 NOT VERIFIABLE for missing stop/target). Historical trades are not reported as parameter-verified.
* The audit proves the arithmetic of the score and the consistency of the trade with its signal (integrity, attribution), not data quality as such and **not profitability**; a correct score does not imply a profitable trade.
* PostgreSQL rehearsal used PG 18 (WASM) and a single connection; lock contention and the real pooler (transaction mode, port 6543) were not exercised.
* One extra short, sequential database session per tick; if the table grows (≈ a few MB/day) a retention rule needs your approval.
* A restart interrupts the API briefly.

## 11. Approvals requested (separately)
(a) run the read-only readiness script on the VPS; (b) the fresh backup and a restore test; (c) push of `hotfix/audit-only-36094e8` to the remote; (d) migration A; (e) code deploy + restart (mode off); (f) the env line + restart (activation); (g) optional least-privilege role. After (f) I will validate that paper trading is unaffected and deliver the first six-model audit report (signals, verified scores, mismatches, missing evidence, stale = NOT ASSESSED, audit errors, trade-attribution mismatches, Strategy Trades consistency — kept apart from data quality and profitability).
