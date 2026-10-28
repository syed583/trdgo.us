# Audit-only deployment — GO / NO-GO report v3 (corrections from the production readiness run)

**Decision: still NO-GO.** The production run (12/12 mandatory, 1 advisory FAIL) exposed two errors in my own earlier work; both are fixed and re-tested locally, but the production gates must be re-run with the corrected checker. Nothing in production was changed (no credentials, privileges, migration, restart, push, deploy or activation); `INTRADAY_WORKFLOW` stays `legacy`; the six strategies and the automatic paper trades are untouched.

Hotfix now: branch `hotfix/audit-only-36094e8`, commit **`f48477466cd823c6d5320c1eadef6bee6ff6626c`** (supersedes `ea1cc04`), 11 files, +1,104/−3 against production `36094e8`; strategy modules still byte-identical to production.

## 1. Corrections (my errors, found thanks to your output)
**A. "No table uses row-level security" was wrong.** I inferred it from the code; the production catalog says **55 of 55 public tables have RLS enabled** (and the role is a member of `anon`, `authenticated`, `service_role`, `supabase_privileged_role`, `pg_read_all_data` …). Consequences: (1) BYPASSRLS is *not* inert — the application works today because its role bypasses RLS; a dedicated NOBYPASSRLS role that does not own the tables would see **no rows** without policies. (2) Phase 0's optional audit role needed policies: the template now contains them (`SELECT` policy on `demo_trades`, `ALL` policy on the audit table). (3) Phase 1 (full app role) works only if the new role **owns** the tables (owners bypass RLS unless `FORCE` is set — the checker now counts forced tables and policies) — still to be confirmed by the next run.
**B. The Data API exposure.** On Supabase every new table in `public` is by default granted to `anon`/`authenticated`, i.e. reachable through the public Data API unless RLS blocks it. The audit table (scores, breakdowns, entries) must therefore be created **private**: migration A now ends with `ENABLE ROW LEVEL SECURITY` and conditional `REVOKE ALL … FROM anon/authenticated` (table and sequence); the application's own table-creation path does the same (`secure_audit_table`). Rehearsed on PostgreSQL 17.5 with emulated Supabase roles and default privileges: a negative control (an unprotected new table) **is** exposed, the audit table is **not** (RLS on, no privileges) — **26/26 checks pass**.
**C. The connection-method check was wrong.** The checker said "SESSION-like … use --port 6543". Port 6543 *is* the Supabase transaction pooler; my statement-level probe cannot prove session semantics under light load (the pooler may hand the same backend back). It now treats 6543 as transaction-pooled by definition, then tries the alternative port (default 5432, `--alt-port` to change), requires that it is the **same server and database** and behaves session-like, and **fails the mandatory check if none exists**. Tested: only 6543 reachable → exit 1; 6543 + a session port → exit 0 and `use --port <that>`. Port 5432 is also the standard session-mode port of the same pooler host; not yet verified from the VPS. **Do not run migration A through 6543.**

## 2. Status of your seven requests
| # | Request | Status |
|---|---|---|
| 1 | Investigate the over-privileged role | PASS (understood): platform-default `postgres` role (CREATEDB, CREATEROLE, BYPASSRLS, REPLICATION, ≥8 memberships), owns 55/55 tables, 55 RLS-enabled, statement timeout 2 min. Runtime DDL is used by ≈25 modules; no code creates roles/databases. Finding stays **FAIL (advisory)** |
| 2 | Least-privilege configuration | Proposed, not applied. Phase 0: audit-only on the existing role (no credential change) + optional `AUDIT_DATABASE_URL` role (policies included). Phase 1: dedicated application role via `gen_app_role_sql.py` (ownership transfer + rollback, rehearsed on PG 17.5; must additionally be rehearsed with RLS enabled and the whole suite against it on a restored copy) |
| 3 | Direct vs 6543 | Corrected: **not 6543** for the DDL; session-mode port required; verified by the checker, **NOT VERIFIED on the VPS until re-run** |
| 4 | Execution role/method | The owner of `demo_trades` = the connecting role (**PASS in your run**); `apply_sql.py --port <session port>` after dry run |
| 5 | Fresh backup + tested rollback | Backup/restore **NOT VERIFIED** (checker `backup` ready, tested with a stand-in `pg_restore`); rollback of migration A/B/C **PASS** on PG 17.5 (26/26) |
| 6 | Non-zero exit on mandatory FAIL | **PASS** (0 / 1 / 2, tested; unit tests) |
| 7 | Re-run readiness + final report | Local re-run PASS; **production re-run NOT VERIFIED** (block below) |

## 3. Gate summary
| Gate | Status |
|---|---|
| PostgreSQL 17.11, primary, read-only session | PASS (your run) |
| Schema = production build's, names free, no locks, 21/90 connections | PASS (your run) |
| Migration role = owner of `demo_trades`, CREATE on `public` | PASS (your run) |
| Session-mode connection for the DDL | **NOT VERIFIED** (previous result was invalid; corrected checker needs a run) |
| Audit table created private (RLS + revoked Data API roles) | PASS (rehearsal); verified on production by the new `postcheck` after migration |
| Application role over-privileged | FAIL, advisory |
| Fresh backup + integrity + restore proof | NOT VERIFIED |
| Branch pushed for the server to fetch | NOT VERIFIED (needs approval) |
| Trading continuity, failure isolation, scorers unchanged | PASS (v1) |

## 4. Added tooling
`prod_gate_checks.py readiness | backup | postcheck` (SHA-256 `a2e71532e382f58adb48091f7e6093f1768a3f1cbbf20e10c279326823bd7e80`): readiness now also reports RLS (enabled/forced/policies), event triggers, Data API role reach and default privileges; `postcheck` (after migration A) verifies exact columns, index, RLS on, no `anon`/`authenticated` privilege, `demo_trades` unchanged and not shrunk (`--expect-demo-rows`).

## 5. Next steps (each needs your explicit approval)
(1) re-run the corrected readiness block; (2) fresh dump → `backup` check (+ ideally a real restore into a scratch PostgreSQL 17); (3) decide Phase 0 / Phase 1; (4) push the hotfix branch; (5) migration A via the session port → `postcheck`; (6) deploy (mode off) + restart; (7) activation line + restart. The first six-model audit report follows step 7.
