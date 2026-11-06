# Security advisories — TRDGO.US production database (documented, accepted for the audit-only release)

Source: read-only production checks of 2026-10-28 (no credential, role name or connection string recorded). Nothing in the audit-only release changes any role, credential or privilege on an existing object.

## A1 — The application connects as the Supabase default `postgres` role (severity: medium; accepted)
* Attributes: not SUPERUSER, but CREATEDB, CREATEROLE, BYPASSRLS, REPLICATION; member of `anon`, `authenticated`, `authenticator`, `service_role`, `supabase_privileged_role`, `pg_read_all_data`, `pg_monitor`, `pg_signal_backend`, `pg_create_subscription`; owns all 55 `public` tables; `statement_timeout` 2 min.
* Risk: a SQL-injection or leaked connection string would have far more reach than the application needs (it could create roles, read every table, bypass row-level security).
* Why it is accepted now: changing the application's role is a credential change with its own rehearsal (ownership transfer, ≈ 25 modules run runtime DDL, RLS interplay). It does not affect the audit-only release.
* Remedy (separate project, separate approval): Phase 1 of the least-privilege plan — a dedicated application role generated from the catalog (`phaseC/gen_app_role_sql.py`), rehearsed on a restored copy, with the whole test suite against it; rollback = the old URL. Because all 55 tables have RLS enabled, the new role must **own** the tables (owners bypass RLS unless FORCE is set) or have explicit policies.

## A2 — Row-level security is enabled everywhere and the application relies on BYPASSRLS; new tables are Data-API-exposed by default (severity: medium; mitigated for the new table)
* 55 of 55 `public` tables have RLS enabled; the application works because its role bypasses RLS. Any role without BYPASSRLS that is not the owner sees nothing unless a policy exists.
* By Supabase default, every new table/sequence in `public` is granted to `anon` and `authenticated`, i.e. reachable through the public Data API unless RLS blocks it.
* Mitigation in this release: migration A (and the application's own creation path) enables RLS on `intraday_signal_audit` with **no policy** and revokes all privileges from `anon` and `authenticated` on the table and its sequence; the `postcheck` verifies the complete ACL (no `PUBLIC`/`anon`/`authenticated`), RLS on, no policy. The Supabase `service_role` (server-side key, bypasses RLS) can read it, as it can read every other table.
* Residual: the same default exposure applies to any future table; a general fix is a platform setting / event trigger or Phase 1.

## Not an advisory but noted
Port 6543 is the transaction pooler and is not used for DDL; DDL uses the session-mode port (5432) after the corrected readiness check confirms it.
