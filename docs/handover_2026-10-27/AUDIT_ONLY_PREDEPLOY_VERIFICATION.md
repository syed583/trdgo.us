# Audit-only — remaining pre-deployment verification (v5)

Hotfix of record: **`db6a72ca12f77d523c3e17ef6e3e6dfa365e9e12`** on production `36094e8` (replaces `a95e7f4`). Nothing was pushed, migrated, deployed, restarted or activated; no credential or role was changed. I have no access to the VPS or to production, so steps 1–3 are VPS actions **you** run from the blocks I give you; what I could prove, I proved locally with real PostgreSQL tools.

## What I verified here, with real PostgreSQL 16.14 tools (initdb, pg_ctl, pg_dump, pg_restore, psql)
A simulated production source was built (Supabase-style roles with default grants to `anon`/`authenticated`/`service_role`, **55 public tables with RLS enabled, 11 policies, `demo_trades` exactly as the production build declares it with 1,010 rows**) and a real `pg_dump -Fc -n public` was taken.
| Check | Result |
|---|---|
| `backup` check against the live source: dump exists, fresh, not empty, **SHA-256 equals the `.sha256` sidecar** (new mandatory check), `pg_restore --list` works, demo_trades data + constraint + both indexes in the TOC, rows read back from the dump (1,010), every dumped id still live, counts reconcile | 11/11 mandatory PASS |
| Scratch restore test (`scratch_restore_test.py`): throw-away cluster on 127.0.0.1:54320 → restore → **55 tables, 55 RLS, 11 policies**, 1,010 rows, content hash of every restored row equals the dump's | PASS (the only restore message is the harmless `schema "public" already exists`) |
| Migration A on the restored schema → strengthened postcheck: 22 exact columns, index, **RLS on**, `anon`/`authenticated` have no privilege, **complete ACL lists no PUBLIC/anon/authenticated** (owner and `service_role` only), no policy, `demo_trades` unchanged | 10/10 PASS |
| `demo_trades` byte-identical after migration A, after the rollback, after re-applying; rollback removes only the audit table; re-apply and a second apply (idempotence) | PASS |
| Scratch cluster stopped and deleted | PASS |
**15/15** scratch checks. Limitations: PostgreSQL 16 locally (production 17.11; the VPS procedure uses the 17 tools), the data is simulated, lock contention with live trading cannot be reproduced locally (mitigated by `lock_timeout 3 s`).

## What only you can run on the VPS (production is only ever *read*; DDL runs only on the scratch cluster)
1. **Install** the verified tool bundle (SHA-256-checked) — block 1.
2. **Readiness** — block 2: must exit 0 and print `use --port 5432`.
3. **Fresh backup + integrity** — block 3: the approved dump script, then the `backup` check (SHA-256, readable rows).
4. **Scratch restore + migration A / rollback on the real production schema** — block 4: restores *that* dump into a throw-away local cluster (never production), applies A, runs the strengthened postcheck, rolls back, re-applies; expects 55 tables (adjust `--expect-tables` if the live count differs).
Requirements/notes: the PostgreSQL 17 tool folder (`C:\pgtools17\pgsql\bin`) must contain `initdb`, `pg_ctl`, `pg_restore`, `psql` (client-only tools are not enough; the script stops with a clear message). Windows PostgreSQL tools drop administrator privileges themselves; if `initdb` still refuses, run block 4 from a non-elevated shell. The scratch cluster binds 127.0.0.1 only, uses a few hundred MB of disk at most, and is deleted at the end. Take the backup for this rehearsal at a quiet time; **a new dump ≤ 30 min old is still required inside the deployment window** (P5), so this one proves the procedure, not the window's backup.

## Remaining risks
* Steps 1–4 are NOT VERIFIED until their outputs are seen; the 12/12 you cited came from the earlier checker whose session-mode test was invalid.
* Port 5432 reachability from the VPS is unproven; the dump script itself uses it, so a successful dump is further evidence.
* Production is PostgreSQL 17.11; the real restore uses tools 17+.
* Everything else (restart ≈ 15–45 s ×2, staleness NOT ASSESSED on this build, advisories A1/A2, audit says nothing about profitability) is unchanged — see `AUDIT_ONLY_FINAL_GO_NO_GO.md`.

## After the outputs: separate approvals
P1 push · B migration · C code + restart #1 · D activation + restart #2 — each its own "go"; approval of one is never approval of another.
