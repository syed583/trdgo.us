# Signal Verification — Audit Mode: deployment plan and safety verification

Nothing in this document has been executed on production. No push, deploy, restart, migration or activation has happened. Everything below is waiting for your explicit approval, stage by stage.

## 0. The one decision that matters: which build to deploy
Production runs **36094e8** (old build). The branch `feature/intraday-session-workflow` is built on **277f30f**, which also carries the 15-minute entry confirmation, entry lock, stale-data gate and gap-aware fills — a *behaviour change* to the daily paper trades (entries only in the regular session, none before ≈10:00). That is a separate cut-over (stages A3–A6 of the runbook) and is **not** what you asked for now.
So audit mode is delivered as a **hotfix branch on top of the exact production commit**: `hotfix/audit-only-36094e8` @ `a95e7f4` (local, not pushed). Its whole diff against 36094e8:

| File | Change |
|---|---|
| `backend/signal_verification.py` (new) | the verifier (read-only maths on the board row) |
| `backend/signal_verification_cli.py` (new) | `report`, `history`, `stats` (read-only) |
| `backend/demo_trade_service.py` | `_row_map` carries two extra read-only keys (`params`, `data`); after the tick's `s.commit()` and `s.close()` a guarded call `_audit_signals(...)`; `_ensure_audit_table()` |
| `backend/models_demo_trades.py` | one new model `IntradaySignalAudit` (`DemoTrade` untouched) |
| `backend/tab_settings.py` | `INTRADAY_VERIFICATION` (mode from env `INTRADAY_VERIFICATION_MODE`, default `off`) |
| `backend/database.py` | the new table name added to the development-isolation list (no effect in production) |
| `backend/migrations/2026_10_27_A_audit_only(.sql / _ROLLBACK.sql)` | the only schema change |
Not touched: the six strategy modules, weights, formulas, thresholds, entry/stop/target/exit code (`_exit_reason`, `_with_levels`, the insert), the frontend, `INTRADAY_WORKFLOW` (does not exist in this build; the behaviour is the production one), V2, shadow, entry lock, anything else. `INTRADAY_WORKFLOW["mode"] = "legacy"` also stays on the feature branch.

## 1. Verification done (all local / throw-away; production never contacted)
| Requirement | Evidence |
|---|---|
| 5. Historical report on production | **Not run by me**: connecting to the production database from this laptop is against the standing rule. Instead `phaseC/prod_readonly_history.py` (+ `run_readonly_history.ps1`) is ready for you to run on the VPS: one **read-only** connection (psycopg `read_only`, the server rejects writes — proven on a test server), SELECT/SHOW only, connection string never printed, writes a JSON report; it also reports the DB version, port, `CREATE` privilege, whether `demo_trades` already has any V2 column, row counts by status and OPEN duplicates. Per model it counts trades that pass/fail the checks history allows (entry score vs the unchanged 55/45 and the trade's side, entry/stop/target bracket, 100 shares). It does **not** re-derive parameter contributions (never stored) and invents nothing. |
| 6. Database type | Production is Supabase PostgreSQL 17 via the pooler (port 6543, transaction mode) per the project records; the script above confirms the live version and port. |
| 6. Backup | Use the existing validated `take_predeploy_dump_FINAL.ps1` (pg_dump ≥17, custom format, read-back validated, refuses a suspiciously small dump). **I have not run it**; it must be done fresh immediately before stage 3. |
| 6. Migration + rollback tested on PostgreSQL | Docker is not running on this machine (I did not start it: it would also start the unrelated signals-platform containers that share the data token), so I used **PGlite 0.5.12 = a real PostgreSQL 18 engine** (WASM), a throw-away server on 127.0.0.1:54329. `phaseC/pg_rehearsal.py`: **26/26 checks** — a release-shaped `demo_trades` with 172 OPEN + 40 closed rows; migration A creates only the audit table and index; `demo_trades` columns, indexes and every row **byte-identical** (md5 over all rows) before/after; idempotent; Legacy ORM insert/update work afterwards; audit hook writes a row; rollback A drops only the audit table and restores the original shape; re-apply works; 240 signals audited in 0.11 s. Optional B and C were rehearsed too. Caveats: PG 18 vs production 17 (the SQL is plain DDL), single connection so lock contention with live trading could not be exercised — mitigated by `lock_timeout 3s` in the file (it fails fast instead of queueing behind trading). |
| 7. Audit cannot roll back / delay / duplicate / interrupt Legacy | see §2 |
| Legacy independent of everything new | on the hotfix build the only DB object added is the audit table; `DemoTrade` mapping unchanged. On the feature branch I additionally made the Legacy ORM mapper *exclude* the V2 columns, so a table without them keeps working (test + PostgreSQL check). |
| Tests | hotfix build: 14 new tests pass; its whole suite 644 passed, 12 skipped, **3 failures that are identical on the untouched 36094e8** (dev-database settings missing in a fresh checkout: `test_dev_isolation` ×2, `test_env_isolation` ×1). Feature branch: 1240+ passed, release_check PASS (also for 277f30f). |

## 2. Why the audit cannot interrupt Legacy trades (item 7) — each point tested
* **After the commit, not inside it.** The tick commits and closes its own session first; the audit then uses a *separate* session. Nothing the audit does shares a transaction with a trade, so it cannot roll one back (test: a failing audit write leaves every trade row byte-identical; on PostgreSQL also "no transaction left open or locked").
* **Verification fails or crashes** → counted (`verify_errors`) and that signal skipped; the tick, and the other signals' audit, continue (test: verifier raising on every call → trades identical, no audit rows).
* **Audit database write fails** (table missing, DB error, timeout) → exception swallowed, the audit session rolled back, counted (`write_errors`), logged; the next tick trades normally (test on SQLite and PostgreSQL: table dropped mid-run, `AFTER` trade opened normally).
* **Delay**: hard time budget 5 s per pass (`max_seconds`); on PostgreSQL the audit session sets `lock_timeout 2s` and `statement_timeout 5s`; measured 0.11 s for 240 signals. It runs inside the tick lock *after* the trades are committed, so the worst case is a later next tick, never a missed or late entry/exit in the current one.
* **Duplicates**: it writes nothing to `demo_trades`. Audit rows are de-duplicated per strategy/symbol/session/direction on (verdict, data status, score, opened); after a restart the first pass may re-log a signal once (extra audit row, never an extra trade).
* **Off by default**; only `off` and `audit` exist (an unknown value is inert). No enforcing mode exists.
* **Schema**: Legacy never reads the audit table; if table creation is impossible the failure is logged once and trading is unaffected.

## 3. Exact production changes (only after your approval)
1. **Code**: `hotfix/audit-only-36094e8` (7 files above) on the server, replacing 36094e8. Deploy method: the same controlled checkout/service procedure you used before (not `update.ps1` from the old checkout; not `git pull` of `main`). Nothing else changes.
2. **Configuration**: add one line to `backend\.env`: `INTRADAY_VERIFICATION_MODE=audit`. Nothing else (no `INTRADAY_MODEL`, no thresholds, no keys). To switch off: remove the line or set `off`.
3. **Database**: one `CREATE TABLE IF NOT EXISTS intraday_signal_audit` + one index (`2026_10_27_A_audit_only.sql`). `demo_trades` is not altered. Table size: ≈ 0.5–3k rows/day (one row per signal *change*), ≈ 3 KB each (JSON breakdown) ≈ a few MB/day; retention (e.g. delete rows older than 30 days) would need your approval. One extra short DB session per tick, sequential — no extra concurrent connection (15-connection ceiling safe).
4. **Restart**: the service `USStockReader` must restart once to load the new code and the env line. A restart interrupts one tick (≈ a minute of paper tracking); open positions are persisted and resume. Pick a quiet time (after 20:00 ET or before 04:00 ET) so no entry window is missed.

## 4. Deployment procedure (each step needs your explicit "go")
* **Step 1 (read-only, can run today):** on the VPS run `phaseC\run_readonly_history.ps1`; send me the printed table / JSON. Gives per-model pass/fail of the existing paper trades and the DB facts.
* **Step 2:** fresh `take_predeploy_dump_FINAL.ps1`; confirm validated (size ≥ 90 % of previous, read-back OK).
* **Step 3 (production DB change):** `python phaseC\apply_sql.py --root C:\apps\us-stock-reader --file 2026_10_27_A_audit_only.sql` (dry run: prints the SHA-256 and statements) then again with `--apply`. One transaction, fails fast (3 s lock timeout), rolls back on any error. Verify: the table exists, `SELECT COUNT(*) FROM demo_trades` unchanged.
* **Step 4 (deploy + restart):** deploy the hotfix build with the env line **absent** (mode off) first; restart; check `/api/health`-type routes, that the 6 boards load and the tracker ticks; confirm Strategy Trades shows the same trades.
* **Step 5 (activate):** add `INTRADAY_VERIFICATION_MODE=audit`, restart once, confirm `SIGNAL_AUDIT_STATS` lines appear in the service log and `intraday_signal_audit` fills while trades continue.
* **Step 6:** collect in the background (see §6).

## 5. Rollback (any time, in this order, no trade data touched)
1. **Switch off**: remove/`off` the env line, restart (or leave running: audit only writes its own table). Trading was never dependent on it.
2. **Code**: redeploy 36094e8 (the audit table is simply ignored by the old code).
3. **Table (optional)**: `apply_sql.py --file 2026_10_27_A_audit_only_ROLLBACK.sql --apply` (drops only `intraday_signal_audit`). If you want the evidence kept, skip this step.
4. **Worst case**: restore from the Step-2 dump — not expected to be needed because no existing table is modified.
Do **not** roll back to 683fce1/main (they fail `release_check.py`, per the project record).

## 6. What will be reported once active (item 11), per strategy
From `signal_verification_cli.py report <date>` and `stats <logfile>`:
* **Total BUY/SELL signals** audited; how many the tracker opened.
* **Verified scores** (mathematical integrity PASS) and **score mismatches** (FAIL, with the failing check: weight, bias scale, contribution, total, 55/45 threshold, entry bracket).
* **Missing / stale evidence** (market-data quality — a *separate* column): `DATA_THIN` (eligible coverage < 70 %, missing readings scored neutral are not verified evidence), `DATA_UNVERIFIED`. **On the 36094e8 build no per-signal timestamps exist, so staleness cannot be judged — only missing evidence is measurable;** staleness becomes measurable only after the 277f30f cut-over (its gate supplies source ages). I will say so rather than report "0 stale".
* **Audit errors**: `verify_errors`, `write_errors`, `budget_skips` from the `SIGNAL_AUDIT_STATS` log lines (counted even when the database write fails).
* Three questions kept apart in every report: (a) is the **score arithmetic** right (integrity), (b) is the **market data behind it** adequate (data quality), (c) was the trade **profitable** — profitability is not part of this audit and no claim about it is made.

## 7. Approval requested
Nothing is deployed. Please approve, separately, any of: Step 1 (read-only report) — Step 2/3 (backup, table) — Step 4/5 (deploy, restart, activate), and the restart window.
