# Audit-only deployment — FINAL pre-deployment GO / NO-GO checklist (v4)

Prepared only. Nothing was migrated, pushed, deployed, restarted or activated; no role, credential or privilege was changed. `INTRADAY_WORKFLOW` stays `legacy`. The exact command sequence (P → B → C → D → E → F) is in `AUDIT_ONLY_EXECUTION_CHECKLIST.md`; this page is the decision sheet.

## Decision: NO-GO until the three open gates below are closed (all other items PASS)
**Open gates:** (1) the corrected readiness run on the VPS exiting 0 with a *session-mode port* advised; (2) the fresh backup completed and verified (`backup` check exit 0, ideally a real scratch restore); (3) your final approval, separately for push, migration, deploy/restart and activation.

## Two corrections to the request's premises
1. **The hotfix is `db6a72ca12f77d523c3e17ef6e3e6dfa365e9e12`, not `a95e7f4`.** Verified by git: `a95e7f4`'s migration A contains **0** lines about row-level security or `REVOKE`, and its application code never secures the table; on Supabase that leaves the audit table reachable by the Data API roles. `db6a72ca…` has `ENABLE ROW LEVEL SECURITY` (line 38) and conditional `REVOKE ALL` from `anon` and `authenticated` on both the table and its sequence (lines 41–47), and the app's own table-creation path does the same. Request 2 is true of `db6a72ca…` only.
2. **Port 5432 is not yet "confirmed".** The 12/12 run you reference was produced by the earlier checker, which reported port 6543 as session-like (it is the transaction pooler). No output from the corrected checker has been seen, so 5432 is **my expectation, not a verified fact**. The corrected checker treats 6543 as transaction-pooled, tries 5432, requires the *same server and database* and session behaviour, and exits 1 if none qualifies. All DDL commands carry `--port 5432` and never use 6543.

## Checklist
| # | Item | Status | Evidence / what closes it |
|---|---|---|---|
| 1 | DDL through the session-mode port 5432, never 6543 | **NOT VERIFIED** | `apply_sql.py … --port 5432`; corrected readiness must print `use --port 5432` and exit 0. Mechanism tested locally: only-6543 → exit 1; 6543 + a session port → exit 0 |
| 2 | Migration A enables RLS and revokes `anon` / `authenticated` | **PASS** (code, SQL, rehearsal) / production proof pending | SQL lines 38–47 (above); rehearsed on PostgreSQL 17.5 with emulated Supabase roles and default privileges: a negative control (an unprotected new table) *is* exposed, the audit table is not. Production proof = `postcheck` after migration A |
| 2b | Post-migration checks of privileges and Data API exposure | **PASS** (tool) | `prod_gate_checks.py postcheck` (v4, SHA-256 `471de53ce2326e87d68c9a7d712dce5a324882283ac7be41a502d0135924dccd`): 22 exact columns, index, **RLS on**, no privilege for `anon`/`authenticated`, the **complete ACL** of the table and its sequence lists no `PUBLIC`/`anon`/`authenticated`, **no policy** opens it, `demo_trades` unchanged and not shrunk. Negative control: granting SELECT to `authenticated` makes it fail (3 FAIL). Optional manual proof through the public API below |
| 3 | Elevated Supabase `postgres` role documented as an advisory; no role/credential/privilege change | **PASS** | `SECURITY_ADVISORIES.md`. The release changes no role, credential or privilege on any existing object; migration A only secures the one table it creates |
| 4a | Backup procedure | **PASS (defined)** | `take_predeploy_dump_277f30f.ps1` (SHA-256 `465B19D1…E7EE`): `pg_dump -Fc`, schema `public`, session pooler 5432, read-only toward the DB, never overwrites, validated by `pg_restore --list` and SHA-256 sidecar |
| 4b | Fresh backup completed and verified | **NOT DONE** | VPS action: dump → `GATE PASS` → `prod_gate_checks.py backup` exit 0 (dump ≤ 30 min old, rows readable from the dump, every dumped id still live, row counts reconcile) |
| 4c | Tested restore plan | **PLAN READY, NOT TESTED** | scratch restore below; I cannot run `pg_restore` here (no PostgreSQL client tools on this machine) |
| 5 | No change to strategy parameters, weights, thresholds, entries, exits, Strategy Trades frontend | **PASS** | `git diff 36094e8 db6a72ca…` on the six strategy modules, `claude_last_service`, `intraday_model`, `intraday_indicators`, `paper_fills`, `frontend`, `deploy`, `requirements.txt`: **empty**. The only removed lines in the whole hotfix are 3: an `except … pass` (now `return`, same effect), the 3-key tail of `_row_map` (extended with read-only keys) and one import line (adds two column types). `tab_settings.py` changes are additions only, none touching the cutoffs or weights. Entry/exit loop: only read-only `have, have0` copies added. 6 real scorers × 400 random inputs: audit agrees 100 %; audit on/off leaves the trade list identical to the cent |
| 6 | `INTRADAY_WORKFLOW=legacy` | **PASS** | the hotfix does not contain the setting (the production build has no workflow switch); the feature branch ships `legacy`; a release check enforces it |
| 7 | Exact deployment / restart / validation / rollback instructions | **PASS (prepared)** | `AUDIT_ONLY_EXECUTION_CHECKLIST.md` sections P, B, C, D, E, F with exact commands, gates and triggers |
| 8 | Expected interruption; safest window | see below | — |
| 9 | Failure isolation (audit cannot roll back/delay/duplicate/interrupt Legacy trades) | **PASS** | audit runs after the tick commits (a second DB connection sees all trades inside the verifier), own session, 5 s budget, per-signal fault isolation, failing/missing audit table changes nothing; PG 17.5 rehearsal |
| 10 | Tests | **PASS** (3 known baseline failures) | hotfix build 656 passed / 12 skipped / 3 failed — identical 3 on untouched `36094e8` (fresh checkout lacks git-ignored `.env.dev`) |

## Security advisories (documented, accepted for this release)
**A1 — the application connects as the Supabase default `postgres` role** (CREATEDB, CREATEROLE, BYPASSRLS, REPLICATION, member of `service_role`, `supabase_privileged_role`, `pg_read_all_data`; owns 55/55 tables). **A2 — row-level security is enabled on all 55 public tables and the application works only because that role bypasses it;** and by default every *new* table in `public` is granted to the Data API roles, so each new table must be created private — migration A does that for the audit table. Remedy (not in this release): Phase 0/1 in report v3. Details: `SECURITY_ADVISORIES.md`.

## Restore plan (to be run; not run)
On a scratch PostgreSQL (the VPS's local PostgreSQL if present, or a throw-away project) — never against production:
```
psql -d postgres -c "CREATE DATABASE restore_check"
psql -d restore_check -c "CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE authenticator NOLOGIN; CREATE ROLE service_role NOLOGIN BYPASSRLS;"
pg_restore --no-owner --no-privileges -d restore_check "<the validated dump>"        # read the error list; PG17-only SET lines are harmless on an older scratch server
psql -d restore_check -c "SELECT COUNT(*) FROM demo_trades"                           # must equal the dump row count
# then, with DATABASE_URL pointing at the SCRATCH database only:
apply_sql.py --file 2026_10_27_A_audit_only.sql --expect-sha256 336960aa… --apply ; prod_gate_checks.py postcheck ; apply_sql.py --file …_ROLLBACK.sql --expect-sha256 c5c862a5… --apply
psql -d restore_check -c "DROP DATABASE"   # (from another database)
```
This also rehearses migration A on the **real production schema** (55 tables, RLS, policies) rather than my emulation, and proves the dump is restorable.

## Optional proof of "no Data API exposure" (after migration A)
Using the project's public API URL and the *anon* key you already hold (do not paste it to me): `curl.exe -s -H "apikey: <ANON_KEY>" -H "Authorization: Bearer <ANON_KEY>" "https://<project>.supabase.co/rest/v1/intraday_signal_audit?select=id&limit=1"` — the safe answers are an HTTP 401/403/404 or `permission denied` / an empty `[]`; **any returned row is a FAIL** (stop and run the F3 rollback). The `postcheck` ACL result is the authoritative test; the curl is a second, external confirmation.

## Expected service interruption and the safest window
* Interruption: two restarts, each **≈ 15–45 s** (estimate; not measured on this VPS) with the site answering 502 and the tracker skipping at most one 60 s cycle; total ≈ 1–2 minutes. Migration A: seconds, no restart, no lock on `demo_trades`.
* Safest window: a **Tuesday–Thursday night, 22:00–01:00 ET** — after the 20:00 ET end of the entry window (no entries, nothing waiting for a tick) and ≥ 3 hours before the 04:00 ET entry burst (time to roll back), with the next morning's first window observable on a weekday. Until the US clock change on **1 Nov** that is **03:00–06:00 VPS time** (runbook: 01:00 VPS = 20:00 ET) = **06:00–09:00 Dubai**; re-derive it after 1 Nov. Avoid 03:30–04:30 ET and any Friday/Sunday-night slot (a problem would otherwise surface into the weekend or straight into Monday's first burst).

## Approval required (separately)
(1) readiness block and backup run on the VPS, outputs to me; (2) P1 push; (3) B migration; (4) C code + restart; (5) D activation + restart — and confirmation that `db6a72ca…` replaces `a95e7f4`.
