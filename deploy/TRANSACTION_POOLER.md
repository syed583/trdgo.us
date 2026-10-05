# Switch to Supabase's transaction pooler (fixes the 15-connection limit)

## Why
Supabase's **session** pooler (port **5432**) caps the project at **15 total
client connections**. One backend uses up to 8, so a single extra process (a
local dev backend, a second worker, a DB GUI) can push past 15 and every query
starts failing with:

```
FATAL: (EMAXCONNSESSION) max clients reached in session mode - max clients are limited to pool_size: 15
```

The **transaction** pooler (port **6543**) multiplexes many clients onto a few
server connections, so the 15-client cap effectively goes away.

## The code is already ready
`backend/database.py` auto-detects the transaction pooler by its port: when
`DATABASE_URL` contains `:6543`, it disables psycopg3 server-side prepared
statements (required — in transaction mode a connection is returned after each
transaction, so prepared statements break). **No code change is needed at deploy
time** — only the connection string.

Pool sizes are also env-overridable now:
- `DB_POOL_SIZE` (default 5)
- `DB_MAX_OVERFLOW` (default 3)

## Steps
1. In the Supabase dashboard: **Project Settings → Database → Connection pooling**.
2. Copy the **Transaction** mode connection string. It looks like:
   ```
   postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```
   (Note the `:6543`. Keep `postgresql+psycopg://` as the scheme if the current
   `DATABASE_URL` uses it — just change host/port to the pooler's.)
3. On the VPS, edit `C:\apps\us-stock-reader\backend\.env` and set `DATABASE_URL`
   to that transaction-pooler string. Keep a backup of the old value.
4. Restart the service:
   ```
   Restart-Service USStockReader
   ```
5. Verify: open the site, confirm login, the user list, and the earnings/board
   pages all load. Check the service log has no `EMAXCONNSESSION` or
   `prepared statement` errors.

## Notes / caveats
- Transaction mode does **not** keep session state across transactions
  (`SET`, temp tables, `LISTEN/NOTIFY`, advisory locks held across statements).
  This app uses short, self-contained queries, so that's fine.
- If you ever need a direct session (e.g. a migration tool that needs prepared
  statements), use the session pooler (5432) or the direct connection for that
  one-off, not the app.
- Local dev should ideally use a **separate** database, never the production one
  — running a second full backend against prod is what caused the outage this
  fixes. If you must point local at prod, set `DB_POOL_SIZE=1 DB_MAX_OVERFLOW=1`.
