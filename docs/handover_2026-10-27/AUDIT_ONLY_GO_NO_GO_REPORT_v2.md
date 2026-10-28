# Audit-only deployment — GO / NO-GO report v2 (role, connection method, backup, exit codes)

**Decision: still NO-GO.** The code and the tooling are ready; what is missing is evidence only you can produce on the VPS (the new readiness run, a verified fresh backup) and the branch push. Nothing in production was changed: no credentials, privileges, migration, restart, push, deploy or activation. `INTRADAY_WORKFLOW` stays `legacy`; the six strategies and the automatic paper trades are untouched (hotfix `ea1cc04` still differs from production `36094e8` only by the audit files listed in report v1).

## 1. The over-privileged role — investigation (PASS = understood; the finding itself remains a FAIL, advisory)
Facts from your run: not superuser; holds CREATEDB, CREATEROLE, BYPASSRLS; `statement_timeout` 2 min; it can CREATE in `public`; DML on `demo_trades`; 21 of 90 connections in use. The profile matches Supabase's **default `postgres` role**; the new checker prints that as a yes/no (`is_the_platform_default_postgres_role`), so it is confirmed rather than assumed. On a managed platform the default role's attributes normally cannot be removed — verify with the platform; the remedy is a different role.
What the code actually needs (searched the whole backend): **no** `CREATE ROLE / DATABASE / EXTENSION / SCHEMA` anywhere; **no** row-level security (so BYPASSRLS has no effect today); but ≈ 25 modules run `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS` or `ALTER TABLE … ADD COLUMN` at runtime. So a dedicated application role needs: LOGIN, USAGE+CREATE on schema `public`, **ownership of its tables** (otherwise those ALTERs fail), DML, sequence use — and none of CREATEDB / CREATEROLE / BYPASSRLS / REPLICATION. That is why the move cannot be a quick `REVOKE`: it needs table ownership transfer and a rehearsal.

## 2. Least-privilege proposal (nothing applied)
* **Phase 0 — now, no credential change, zero trading risk:** deploy audit-only on the existing connection. Optionally give *only the audit* its own tiny role through `AUDIT_DATABASE_URL` (template `2026_10_28_A_audit_role_TEMPLATE.sql`: SELECT on `demo_trades`, SELECT/INSERT on the audit table, `CONNECTION LIMIT 2`, 2 s lock / 5 s statement timeouts; tested that the audit then uses its own connection). Trading never uses it. Requires a new secret in `.env` = a configuration change that needs your approval; skip it and the audit runs on the existing role.
* **Phase 1 — separate project, separate approval:** a dedicated application role. `phaseC/gen_app_role_sql.py` reads the live catalog (read-only) and **generates, without executing,** the `CREATE ROLE` + ownership-transfer script and its exact rollback. Rehearsed on a PostgreSQL 17.5 engine: after the up script the table is owned by the new role with all four dangerous flags false and the data md5 identical; after the rollback the owner is back, the role is gone, data identical. Production path: (1) restore a fresh dump into a scratch PostgreSQL 17, (2) run the up script there, (3) run the full backend suite against the new role, (4) canary, (5) change `DATABASE_URL` at a quiet time; rollback = put the old URL back (the old role stays valid throughout) and run the rollback script. Risks: a missed grant would break a rarely used module — hence the rehearsal; connection limit; Supabase-managed objects must not be touched (the generator only moves tables the connecting role owns and lists the rest).

## 3. Direct connection or the transaction pooler (port 6543)? — recommendation: NOT 6543 for the DDL; NOT VERIFIED which alternative works from the VPS
Your production URL uses port 6543 (transaction pooler). A transaction pooler multiplexes statements over shared server connections and does not guarantee session state; schema changes should run on a **session-mode or direct** connection (the project's own backup script already uses the session pooler on 5432 for the same reason). The checker now **measures** it instead of assuming: over 8 statements it counts the server backends and whether a session label survives (SESSION-like vs TRANSACTION-pooled) on the current port and on 5432, and fails the mandatory check "a SESSION-mode connection is available for the DDL" if none exists (then a direct connection string is needed). `apply_sql.py --port 5432` was added for it; the DDL file itself is one explicit transaction with `lock_timeout 3s`. Tested here only against local servers; the real answer comes from your run.

## 4. Migration execution role and method
Role: the **owner of `demo_trades`** (the checker prints PASS/FAIL for "the connecting role owns demo_trades"). It also has CREATE on `public`, which migration A needs, and no other privilege is required. Method: `apply_sql.py --file 2026_10_27_A_audit_only.sql --port <the port the checker recommends>` — dry run first (prints SHA-256 and statements), then `--apply`; one transaction; fails fast; the application is not stopped. After Phase 1 the owner would be the dedicated role.

## 5. Fresh backup and tested rollback
* Backup: **NOT VERIFIED** — not taken (your action; the existing `take_predeploy_dump_FINAL.ps1` only reads the database).
* New: `prod_gate_checks.py backup` verifies it independently, all mandatory: file exists, **≤ 30 minutes old**, not empty, not < 90 % of the previous dump, `pg_restore` is 17+, `--list` succeeds, the TOC contains `demo_trades` data, its constraint and both indexes, the `demo_trades` rows **can be read back out of the dump**, every dumped id still exists live (nothing deleted), and the live row count equals the dump plus the trades opened since. It prints the restore recipe for a scratch PostgreSQL 17 (not executed). Tested with a stand-in `pg_restore`: good dump → 0 mandatory failures; dump containing ids the live table lacks → exit 1; stale dump → exit 1. Not tested against a real dump file (none exists here). A real restore into a scratch database remains your/our next step.
* Rollback tested: migration A + rollback rehearsed on PostgreSQL **17.5** (PGlite 0.3.15; the earlier 26/26 was on 18): **24/24** — the two bulk-insert timing checks were skipped because that old WASM build traps on one 240-row INSERT (an emulator limit; the same checks passed on the 18 engine at 0.11 s). Data md5 identical across apply, rollback and re-apply; Legacy ORM insert/update work; a failing audit write changes nothing.

## 6. Mandatory FAIL ⇒ non-zero exit — DONE and tested
`prod_gate_checks.py readiness|backup`: **exit 0** only if every mandatory check passes; **exit 1** for any mandatory FAIL or NOT VERIFIED; **exit 2** if the checker itself cannot run (e.g. unreachable database). Verified: clean run → 0; name collision (an existing `intraday_signal_audit`) → 1; `--require-least-privilege` makes the role finding mandatory → 1; unreachable DB → 2; backup variants → 1. The least-privilege finding is **advisory by default** (printed as `FAIL (advisory)`), because it is a decision about Phase 1 rather than a blocker for the audit-only table; run with `--require-least-privilege` to make it block. 6 unit tests (`test_prod_gate_checks.py`) lock this in. (Caution for shells: a pipe such as `| tail` hides the exit code.)

## 7. Re-run readiness — local PASS, production NOT VERIFIED
Local run on PostgreSQL 17.5 with a production-shaped schema: 12/12 mandatory pass, 1 advisory (role). **The production re-run needs your paste block** (below this report in the chat) — until it prints `MANDATORY … 0 FAIL … GO-eligible` and recommends a session-mode port, the gate stays closed. Previous production run (v1): 12 PASS, 1 FAIL (role), PostgreSQL **17.11**, schema exactly as expected, no collisions, no heavy locks, 21/90 connections.

## 8. Gate summary
| Gate | Status |
|---|---|
| PostgreSQL 17.11, primary, read-only session | PASS (your run) |
| Schema, name collisions, locks, connection headroom | PASS (your run) |
| Application role over-privileged | FAIL, advisory (Phase 0 audit role / Phase 1 plan ready) |
| Session-mode connection for DDL; owner role | NOT VERIFIED until the new run |
| Fresh backup + integrity + restore recipe | NOT VERIFIED (tool ready) |
| Real restore into a scratch database | NOT VERIFIED |
| Rollback SQL rehearsed on PG 17.5 | PASS |
| Exit codes for mandatory failures | PASS |
| Branch pushed so the server can fetch it | NOT VERIFIED (needs your approval) |
| Trading continuity / failure isolation / six scorers unchanged | PASS (report v1) |

## 9. What I need from you
1. Run the readiness block (read-only) and send the whole output; 2. run `take_predeploy_dump_FINAL.ps1` then the same script with `backup` (read-only toward the DB); 3. your decisions: Phase 0 audit role yes/no, Phase 1 yes/no; 4. later, separately: push, migration A, deploy+restart, activation.
