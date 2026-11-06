# Audit-only — pre-deployment results received from the VPS (2026-10-28, VPS time 11:15–11:22 = 06:15–06:22 ET)

Hotfix of record `db6a72ca12f77d523c3e17ef6e3e6dfa365e9e12` on production `36094e8`. Nothing pushed, migrated, deployed, restarted or activated.

| # | Requirement | Result | Evidence (from the pasted VPS output) |
|---|---|---|---|
| 1 | Corrected readiness; port 5432 is a valid session-mode connection | **PASS** | PostgreSQL **17.11**, primary, read-only session. Port 6543 = transaction pooler (by design). Port **5432: SESSION-like, 1 backend over 8 statements, same server and database** → `use --port 5432`. 12/12 mandatory, exit 0, 2 advisories. Independent corroboration: the approved dump script (which uses 5432) connected and dumped successfully. Not yet proven: a multi-statement DDL transaction through 5432 (only the migration itself proves that; `lock_timeout 3 s` + one transaction make a failure harmless) |
| 2 | Fresh backup, integrity, rows, SHA-256 | **PASS** | `trdgo_us_20261028-1121_predeploy.dump` 70,442,903 bytes, SHA-256 `A3CE897CA84DBA1A937E149056EEC24F736820EBAD743616AE4023911AE9FE02`, 486 archive entries, 55 tables with data, all 6 key tables; pg_dump/pg_restore 17.11. `backup` check **12/12 mandatory, exit 0**: SHA-256 equals sidecar, `--list` OK, demo_trades data + constraint + both indexes in the TOC, **1,010 rows read back**, every dumped id live, counts reconcile (opened since dump: 0). A second dump (`…1120…`, SHA `B504F3D5…D5F1`) also validated. These prove the procedure; **a new dump ≤ 30 min old is required inside the deployment window (P5)** |
| 3 | Scratch restore with the real production schema | **NOT DONE** | `STOPPED: initdb / pg_ctl / pg_restore / psql not all found in C:\pgtools17\pgsql\bin` (exit 2) — that folder holds client tools only. Nothing was restored anywhere |
| 4 | Rehearse migration A + rollback on the scratch database | **NOT DONE** (depends on 3) | Rehearsed locally with real PostgreSQL 16.14 tools (15/15) and PostgreSQL 17.5 (26/26) on simulated 55-table RLS schemas; not on the real schema |
| 5 | RLS / privileges / sequence / Data API checks by the strengthened postcheck | **NOT DONE** on a restored copy; production proof only after migration A | the strengthened postcheck passed 10/10 in the local rehearsals |
| 6 | `demo_trades` unchanged | **PASS so far** | 1,010 rows before and after both dumps; the live table has not changed; the scratch/real-schema byte-identical check is part of step 3 |

## New facts from this output
* **Supabase event trigger `ensure_rls` exists** (plus `pgrst_ddl_watch`, `pgrst_drop_watch`, `issue_pg_graphql_access`, `issue_pg_net_access`, `issue_pg_cron_access`, `issue_graphql_placeholder`): the platform enables RLS on new tables automatically; migration A's `ENABLE ROW LEVEL SECURITY` is idempotent on top of it. The PostgREST watch triggers mean a new table in `public` is announced to the Data API immediately — hence the explicit `REVOKE` for `anon`/`authenticated` in migration A stays essential.
* **All 55 tables: RLS on, 0 forced, 0 policies;** `anon` and `authenticated` hold table-level SELECT on `demo_trades` (and, by default privileges, on every table) — blocked only by RLS with no policy. Default privileges grant new tables to the Data API roles (advisory 2).
* The role is a member of `service_role` and `supabase_privileged_role` etc. (advisory 1).
* Connections: 20–21 of 90 in use; no heavy lock; no long transaction.

## Next
`initdb`/`pg_ctl` are needed for step 3. The dump script found a PostgreSQL 17 install at `C:\Program Files\PostgreSQL\17\bin` — if that folder has `initdb.exe` and `pg_ctl.exe` it can serve as `--pg-bin` (separate data directory and port; no interaction with any service). If not, a full PostgreSQL 17 binary set is required (needs your decision), or the real-schema rehearsal is explicitly waived with the local rehearsals as the only evidence.
